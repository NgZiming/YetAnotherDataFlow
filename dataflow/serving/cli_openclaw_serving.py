"""
OpenClaw Serving via CLI (基于 openclaw CLI 命令，支持并发)

通过 openclaw CLI 命令执行任务，每个请求在独立的 session 中运行，
使用线程池并发处理多个请求。

特点:
- 不使用 WebSocket API，直接调用 openclaw CLI 命令
- 每个请求在 agent 的不同 session 中执行（通过 /new 创建）
- 支持并发处理，每次调用创建临时线程池
- 预先创建 worker 数量的 agent，避免重复创建失败

使用示例:
    from dataflow.serving import create_openclaw_serving

    serving = create_openclaw_serving(
        agent_id="main",
        model="custom/Qwen3.5-122B-A10B",
        max_workers=4,
    )
    responses = serving.generate_from_input(["问题 1", "问题 2"])
"""

from __future__ import annotations

import json
import subprocess
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any, List

from dataflow.core import LLMServingABC
from dataflow.logger import get_logger

# 导入二进制文件生成模块
try:
    from dataflow_extensions.operators.agentic.generate_binary_files import (
        generate_file,
    )
    HAS_BINARY_GENERATOR = True
except ImportError:
    HAS_BINARY_GENERATOR = False
    generate_file = None

# OpenClaw 基础目录
OPENCLAW_BASE = Path.home() / ".openclaw"


# ============================================================================
# Agent 管理函数
# ============================================================================


def _agent_store_dir(agent_id: str) -> Path:
    """获取 agent store 目录路径。"""
    base = OPENCLAW_BASE / "agents"
    direct = base / agent_id
    if direct.exists():
        return direct
    normalized = base / agent_id.lower()
    if normalized.exists():
        return normalized
    return direct


def _workspace_dir(agent_id: str) -> Path:
    """获取 agent workspace 目录路径。"""
    if agent_id == "main":
        return OPENCLAW_BASE / "workspace"
    return OPENCLAW_BASE / "agents" / agent_id


def _list_existing_agents() -> set[str]:
    """列出所有已存在的 agents（小写标识符集合）。"""
    try:
        result = subprocess.run(
            ["openclaw", "agents", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        agents = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("- "):
                name = line[2:].split()[0]
                if name:
                    agents.add(name.lower())
        return agents
    except FileNotFoundError:
        return set()


def create_agent(agent_id: str, model: str) -> None:
    """
    创建新的 agent。

    Args:
        agent_id: Agent 标识符
        model: 模型名称

    Raises:
        RuntimeError: 创建失败时抛出
    """
    ws = str(_workspace_dir(agent_id))
    Path(ws).mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "openclaw",
            "agents",
            "add",
            agent_id,
            "--model",
            model,
            "--workspace",
            ws,
            "--non-interactive",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create agent {agent_id}: {result.stderr.strip()}"
        )


# ============================================================================
# Session 管理函数
# ============================================================================


def _resolve_transcript_path(agent_id: str) -> Path:
    """
    解析新生成的 session 文件路径。

    通过两种方式查找：
    1. 优先通过 sessions.json 索引查找最新的 session
    2. fallback 到查找最新修改的 .jsonl/.ndjson 文件

    Args:
        agent_id: Agent 标识符

    Returns:
        Session 文件路径

    Raises:
        FileNotFoundError: 超时后文件仍未找到
    """
    agent_dir = _agent_store_dir(agent_id)
    sessions_dir = agent_dir / "sessions"

    # 持续等待直到找到 session 文件（最多 60 秒）
    start_time = time.time()
    timeout = 60  # 60 秒超时

    while time.time() - start_time < timeout:
        # 策略 1: 通过 sessions.json 索引查找
        sessions_json = sessions_dir / "sessions.json"
        if sessions_json.exists():
            try:
                payload = json.loads(sessions_json.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    newest_entry = None
                    newest_ts = -1
                    for entry in payload.values():
                        if not isinstance(entry, dict) or "sessionId" not in entry:
                            continue
                        ts = entry.get("updatedAt", 0)
                        if isinstance(ts, (int, float)) and ts > newest_ts:
                            newest_ts = ts
                            newest_entry = entry
                    if newest_entry:
                        sid = newest_entry["sessionId"]
                        for candidate in (
                            sessions_dir / f"{sid}.jsonl",
                            sessions_dir / f"{sid}.ndjson",
                        ):
                            if candidate.exists():
                                return candidate
            except (json.JSONDecodeError, OSError):
                pass

        # 策略 2: 查找最新修改的 .jsonl/.ndjson 文件
        if sessions_dir.exists():
            candidates = list(sessions_dir.rglob("*.jsonl")) + list(
                sessions_dir.rglob("*.ndjson")
            )
            if candidates:
                return max(candidates, key=lambda p: p.stat().st_mtime)

        time.sleep(1.0)

    raise FileNotFoundError(f"Agent {agent_id} 的 session 文件在 {timeout} 秒内未生成")


def load_session_new_messages(
    agent_id: str, last_timestamp: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    加载 session 文件中指定时间之后的新消息。

    Args:
        agent_id: Agent 标识符
        last_timestamp: 上次读取的时间戳（秒），只返回此时间之后的消息

    Returns:
        新消息列表

    Raises:
        FileNotFoundError: session 文件不存在
        json.JSONDecodeError: 解析 session 文件失败
    """
    transcript_path = _resolve_transcript_path(agent_id)

    messages = []
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        # 检查消息时间戳
        msg_ts = msg.get("timestamp", 0)
        if isinstance(msg_ts, (int, float)) and last_timestamp is not None:
            if msg_ts <= last_timestamp:
                continue
        messages.append(msg)

    return messages


# ============================================================================
# 响应处理函数
# ============================================================================


def _execute_single_query(
    agent_id: str,
    user_query: str,
    timeout: int,
    logger: Any,
) -> str:
    """
    执行单个查询请求（使用预先创建的 agent）。

    Args:
        agent_id: Agent 标识符
        user_query: 用户查询内容
        timeout: 超时时间（秒）
        logger: 日志对象

    Returns:
        助手的回复文本，如果执行失败则返回空字符串
    """
    # 记录 /new 之前的时间戳，只获取新 session 中的消息
    last_timestamp = time.time()

    try:
        # 先执行 /new 创建新 session
        new_cmd = [
            "openclaw",
            "agent",
            "--agent",
            agent_id,
            "-m",
            "/new",
        ]
        logger.info(f"创建新 session: {' '.join(new_cmd)}")
        new_result = subprocess.run(
            new_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if new_result.returncode != 0:
            logger.warning(f"/new 命令失败：{new_result.stderr.strip()}")

        # 执行用户查询
        cmd = [
            "openclaw",
            "agent",
            "--agent",
            agent_id,
            "-m",
            user_query,
        ]
        logger.info(" ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise Exception(result.stderr)
    except subprocess.TimeoutExpired:
        logger.warning(f"查询超时 ({timeout}s)")
        raise
    except Exception:
        logger.exception("查询失败")
        raise

    # 只读取新 session 中的消息
    messages = load_session_new_messages(agent_id, last_timestamp)
    return json.dumps({"messages": messages}, ensure_ascii=False) if messages else ""


# ============================================================================
# CLIOpenClawServing 类
# ============================================================================


class CLIOpenClawServing(LLMServingABC):
    """
    通过 CLI 执行 OpenClaw 任务的 Serving 类。

    特点:
    - 使用 openclaw CLI 命令执行任务，不依赖 WebSocket API
    - 每个请求在独立的 session 中运行（通过 /new 创建）
    - 预先创建 worker 数量的 agent，避免重复创建失败
    - 对象可以被 pickle 拷贝（适合分布式任务场景）
    """

    def __init__(
        self,
        agent_id: str = "main",
        model: Optional[str] = None,
        timeout: int = 300,
        create_if_missing: bool = True,
        max_workers: int = 4,
    ):
        """
        初始化 CLIOpenClawServing。

        Args:
            agent_id: Agent 标识符，默认 "main"
            model: 模型名称，如果 agent 不存在且 create_if_missing=True 则用于创建
            timeout: 单次查询超时时间（秒），默认 300
            create_if_missing: 如果 agent 不存在是否自动创建，默认 True
            max_workers: 并发 worker 数量，默认 4
        """
        self.logger = get_logger()

        self.agent_id = agent_id
        self.model: str = model or "vllm//data/share/models/Qwen3.5-122B-A10B/"
        self.timeout = timeout
        self.create_if_missing = create_if_missing
        self.max_workers = max_workers
        self._initialized = False
        self._worker_agents: List[str] = []

    def _ensure_agent(self) -> None:
        """确保 agent 存在，不存在则创建。"""
        existing = _list_existing_agents()

        if self.agent_id.lower() not in existing:
            if self.create_if_missing and self.model:
                self.logger.info(f"创建 agent {self.agent_id} (model={self.model})...")
                create_agent(self.agent_id, self.model)
            else:
                raise RuntimeError(
                    f"Agent {self.agent_id} 不存在，请设置 model 参数或手动创建"
                )

    def _setup_worker_agents(self) -> None:
        """为每个 worker 预先创建独立的 agent。"""
        if self._worker_agents:
            return

        existing = _list_existing_agents()
        self._worker_agents = []

        for i in range(self.max_workers):
            worker_agent_id = f"{self.agent_id}-worker-{i}"
            if worker_agent_id.lower() not in existing:
                if self.create_if_missing and self.model:
                    self.logger.info(
                        f"创建 worker agent {worker_agent_id} (model={self.model})..."
                    )
                    create_agent(worker_agent_id, self.model)

                    # 等待 agent 注册成功
                    for attempt in range(10):
                        time.sleep(0.5)
                        check_result = subprocess.run(
                            ["openclaw", "agents", "list"],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if worker_agent_id.lower() in check_result.stdout.lower():
                            self.logger.info(f"Worker agent {worker_agent_id} 注册成功")
                            break
                        if attempt == 9:
                            self.logger.warning(
                                f"Worker agent {worker_agent_id} 注册超时，继续..."
                            )
                else:
                    raise RuntimeError(
                        f"Worker agent {worker_agent_id} 不存在，请设置 model 参数"
                    )
            self._worker_agents.append(worker_agent_id)

        self.logger.info(f"Worker agents 就绪：{self._worker_agents}")

    def start_serving(self) -> None:
        """启动服务（确保 agent 存在）。"""
        if self._initialized:
            return

        self._ensure_agent()
        self._setup_worker_agents()
        self._initialized = True
        self.logger.info(
            f"OpenClaw CLI Serving 已就绪 "
            f"(agent={self.agent_id}, workers={self.max_workers}, timeout={self.timeout}s)"
        )

    def generate_from_input(
        self,
        user_inputs: List[str],
        system_prompt: str,
        json_schema: dict,
        input_files_data: Optional[List[Dict]] = None,
        input_files_data_key: Optional[str] = None,
        storage = None,
    ) -> List[str]:
        """
        生成文本响应（并发执行，每个请求使用独立 worker agent）。

        Args:
            user_inputs: 用户输入列表
            system_prompt: 系统提示词（CLI 模式下不使用）
            json_schema: JSON schema（CLI 模式下不使用）
            input_files_data: 可选，FileContextGenerator 输出的文件内容数据列表
            input_files_data_key: 可选，如果提供则从 storage 读取此 key 对应的数据
            storage: 可选，DataFlowStorage 实例，用于读取 input_files_data_key

        Returns:
            回复列表，长度与输入相同，失败位置为空字符串
        """
        if not self._initialized:
            self.start_serving()

        if not user_inputs:
            return []

        # 如果有文件生成需求，先生成二进制文件
        generated_files = []
        if HAS_BINARY_GENERATOR:
            if input_files_data:
                generated_files = self._generate_binary_files_from_data(input_files_data)
            elif input_files_data_key and storage:
                generated_files = self._generate_binary_files_from_key(
                    input_files_data_key, storage
                )
            
            if generated_files:
                self.logger.info(f"生成 {len(generated_files)} 个二进制文件")

        self.logger.info(
            f"处理 {len(user_inputs)} 个请求 (workers={self.max_workers})..."
        )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for i, question in enumerate(user_inputs):
                # 分配 worker agent（轮询）
                worker_agent_id = self._worker_agents[i % len(self._worker_agents)]
                future = executor.submit(
                    _execute_single_query,
                    worker_agent_id,
                    question,
                    self.timeout,
                    self.logger,
                )
                futures[future] = i

            results: list[str] = [""] * len(user_inputs)
            completed = 0
            failed = 0

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                    completed += 1
                except Exception as e:
                    self.logger.error(f"生成失败 (idx={idx}): {e}")
                    results[idx] = ""
                    failed += 1

            self.logger.info(f"完成：{completed} 成功，{failed} 失败")
            return results

    def generate_embedding_from_input(self, texts: List[str]) -> List[List[float]]:
        """生成嵌入向量（CLI 模式不支持，返回空向量）。"""
        self.logger.warning("CLI 模式不支持 embedding，返回空向量")
        return [[] for _ in texts]

    def cleanup(self) -> None:
        """清理资源。"""
        self._initialized = False
        self.logger.info("OpenClaw CLI Serving 已清理")

    def _generate_binary_files_from_data(
        self, files_data: List[Dict], output_key: Optional[str] = None
    ) -> List[Path]:
        """
        从文件内容数据列表生成二进制文件。

        Args:
            files_data: FileContextGenerator 输出的数据列表，每个 row 包含
                       output_key 对应的 {filename: content_data} 字典
            output_key: FileContextGenerator 的输出 key，如果提供则只处理此 key

        Returns:
            生成的文件路径列表
        """
        generated = []
        workspace = str(_workspace_dir(self.agent_id))

        for row_data in files_data:
            # 如果有指定的 output_key，只处理这个 key
            keys_to_process = [output_key] if output_key else row_data.keys()
            
            for key in keys_to_process:
                file_contents = row_data.get(key)
                if not isinstance(file_contents, dict):
                    continue
                
                for filename, content_data in file_contents.items():
                    if not content_data or not isinstance(content_data, dict):
                        self.logger.warning(f"跳过无效内容：{filename}")
                        continue
                    
                    try:
                        # 生成文件到 workspace
                        output_path = Path(workspace) / Path(filename).name
                        generate_file({"filename": filename, "content": content_data}, workspace)
                        generated.append(output_path)
                        self.logger.info(f"生成文件：{output_path}")
                    except Exception as e:
                        self.logger.error(f"生成文件失败 {filename}: {e}")

        return generated

    def _generate_binary_files_from_key(
        self, data_key: str, storage=None
    ) -> List[Path]:
        """
        从 storage 中读取指定 key 的数据并生成二进制文件。

        Args:
            data_key: storage 中的 key，对应 FileContextGenerator 的 output_key
            storage: DataFlowStorage 实例（可选）

        Returns:
            生成的文件路径列表
        """
        if storage is None:
            self.logger.error("storage 参数不能为 None")
            return []

        try:
            dataframe = storage.read("dataframe")
            rows = dataframe.to_dict("records")
            return self._generate_binary_files_from_data(rows)
        except Exception as e:
            self.logger.error(f"读取 storage 失败：{e}")
            return []


def create_openclaw_serving(
    agent_id: str = "main", model: Optional[str] = None, max_workers: int = 4, **kwargs
) -> CLIOpenClawServing:
    """
    创建 CLIOpenClawServing 实例的工厂函数。

    Args:
        agent_id: Agent 标识符，默认 "main"
        model: 模型名称
        max_workers: 并发 worker 数量，默认 4
        **kwargs: 其他参数（timeout, create_if_missing）
    """
    return CLIOpenClawServing(
        agent_id=agent_id, model=model, max_workers=max_workers, **kwargs
    )
