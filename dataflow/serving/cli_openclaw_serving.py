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

from tqdm import tqdm

from dataflow.core import LLMServingABC
from dataflow.logger import get_logger

# 导入二进制文件生成模块
from dataflow.utils.generate_binary_files import generate_file

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


def _is_agent_locked(agent_id: str) -> bool:
    """检查 agent 是否有任何 session 被锁定。

    Session lock 文件格式：{session_id}.jsonl.lock
    位置：agent 的 sessions/ 目录下

    Args:
        agent_id: Agent 标识符

    Returns:
        True 如果检测到任何 session lock 文件，False 否则
    """
    try:
        agent_dir = _agent_store_dir(agent_id)
        sessions_dir = agent_dir / "sessions"
        # 检查 sessions 目录下是否存在任何 .jsonl.lock 文件
        if sessions_dir.exists():
            for lock_file in sessions_dir.glob("*.jsonl.lock"):
                return True
        return False
    except Exception:
        return False


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


def _execute_query_once(
    agent_id: str,
    user_query: str,
    timeout: Optional[int],
    logger: Any,
    input_files_data: Dict,
) -> str:
    """
    执行单个查询请求（单次尝试，不重试）。

    Args:
        agent_id: Agent 标识符
        user_query: 用户查询内容
        timeout: 超时时间（秒），None 表示不设置超时
        logger: 日志对象
        input_files_data: 该 query 对应的文件内容数据（FileContextGenerator 输出）

    Returns:
        助手的回复文本

    Raises:
        Exception: 执行失败时抛出异常
    """
    # 记录 /new 之前的时间戳，只获取新 session 中的消息
    last_timestamp = time.time()

    # 清理 assets 目录（失败或结束时也会清理）
    assets_dir = _workspace_dir(agent_id) / "assets"

    def _cleanup_assets():
        """清空 assets 目录"""
        if assets_dir.exists():
            import shutil

            try:
                shutil.rmtree(assets_dir)
            except Exception as e:
                logger.error(f"清空 assets 目录失败：{e}")

    new_file_paths: list[str] = []
    # 在 /new 之前生成二进制文件
    if input_files_data:
        try:
            # 统一使用 /workspace/assets/ 目录
            assets_dir.mkdir(parents=True, exist_ok=True)
            for filename, content_data in input_files_data.items():
                if not content_data or not isinstance(content_data, dict):
                    continue
                # 所有文件都放在 assets 目录下，忽略原始路径
                new_path = assets_dir / Path(filename).name
                # 传给 generate_file：只传文件名，output_dir 是 assets 目录
                generate_file(
                    {"filename": new_path.name, "content": content_data},
                    str(assets_dir),
                )
                new_file_paths.append(str(new_path))
        except Exception as e:
            logger.error(f"生成文件失败：{e}")
            _cleanup_assets()
            raise

    try:
        # 先执行 /new 创建新 session
        new_cmd = [
            "openclaw",
            "agent",
            "--agent",
            agent_id,
            "--local",
            "-m",
            "/new",
        ]
        new_result = subprocess.run(
            new_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if new_result.returncode != 0:
            # 检查是否因为 session 被锁导致失败
            error_msg = new_result.stderr
            if "locked" in error_msg.lower() or "lock" in error_msg.lower():
                logger.warning(f"检测到 session 被锁，尝试清理 lock 文件...")
                # 清理 lock 文件（在 sessions/ 目录下）
                agent_dir = _agent_store_dir(agent_id)
                sessions_dir = agent_dir / "sessions"
                deleted = 0
                if sessions_dir.exists():
                    for lock_file in sessions_dir.glob("*.jsonl.lock"):
                        try:
                            lock_file.unlink()
                            deleted += 1
                        except Exception as e:
                            logger.error(f"删除 lock 文件失败 {lock_file}: {e}")
                logger.info(f"已删除 {deleted} 个 lock 文件，将重试 /new 命令")
                # 重试一次 /new
                new_result = subprocess.run(
                    new_cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            if new_result.returncode != 0:
                raise RuntimeError(f"/new 命令失败：{new_result.stderr}")

        if len(new_file_paths) > 0:
            user_query += f"\n下面是任务相关的文件：\n{new_file_paths}"
        # 执行用户查询（使用 --local 绕过网关配对）
        cmd = [
            "openclaw",
            "agent",
            "--agent",
            agent_id,
            "--local",
            "-m",
            user_query,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,  # None 表示不设置超时
            check=False,
        )
        if result.returncode != 0:
            raise Exception(result.stderr)
        logger.info(f"请求结束： {result.stdout[:50]}...")
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        raise
    finally:
        # 清空 assets
        _cleanup_assets()

    # 只读取新 session 中的消息
    messages = load_session_new_messages(agent_id, last_timestamp)
    return json.dumps({"messages": messages}, ensure_ascii=False) if messages else ""


def _execute_single_query(
    agent_id: str,
    user_query: str,
    timeout: int,
    logger: Any,
    input_files_data: Dict,
    max_retries: int = 3,
) -> str:
    """
    执行单个查询请求（使用预先创建的 agent，带重试）。

    Args:
        agent_id: Agent 标识符
        user_query: 用户查询内容
        timeout: 超时时间（秒）
        logger: 日志对象
        input_files_data: 该 query 对应的文件内容数据（FileContextGenerator 输出）
        max_retries: 最大重试次数，默认 3

    Returns:
        助手的回复文本，如果执行失败则返回空字符串
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return _execute_query_once(
                agent_id, user_query, timeout, logger, input_files_data
            )
        except Exception as e:
            last_error = e

            if attempt < max_retries:
                # 指数退避：1s, 2s, 4s...
                backoff_time = 2**attempt
                time.sleep(backoff_time)
            else:
                logger.error(f"请求失败 (after {max_retries + 1} attempts): {e}")

    return ""


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
        timeout: Optional[int] = None,
        create_if_missing: bool = True,
        max_workers: int = 4,
        max_retries: int = 3,
    ):
        """
        初始化 CLIOpenClawServing。

        Args:
            agent_id: Agent 标识符，默认 "main"
            model: 模型名称，如果 agent 不存在且 create_if_missing=True 则用于创建
            timeout: 单次查询超时时间（秒），None 表示不设置超时，默认 None
            create_if_missing: 如果 agent 不存在是否自动创建，默认 True
            max_workers: 并发 worker 数量，默认 4
            max_retries: 请求失败时的最大重试次数，默认 3
        """
        self.logger = get_logger()

        self.agent_id = agent_id
        self.model: str = model or "vllm//data/share/models/Qwen3.5-122B-A10B/"
        self.timeout = timeout
        self.create_if_missing = create_if_missing
        self.max_workers = max_workers
        self.max_retries = max_retries
        self._initialized = False
        self._worker_agents: List[str] = []

    def _ensure_agent(self) -> None:
        """确保 agent 存在，不存在则创建。"""
        existing = _list_existing_agents()

        if self.agent_id.lower() not in existing:
            if self.create_if_missing and self.model:
                create_agent(self.agent_id, self.model)
            else:
                raise RuntimeError(
                    f"Agent {self.agent_id} 不存在，请设置 model 参数或手动创建"
                )

    def _setup_and_check_worker_agents(self, initial_setup: bool = False) -> None:
        """设置或检查所有 worker agents 的健康状态，重建不可用的。

        Args:
            initial_setup: 如果是 True，只在初始化时设置，不检查现有 agent
        """
        existing = _list_existing_agents()
        new_worker_agents = []

        for i in range(self.max_workers):
            worker_agent_id = f"{self.agent_id}-worker-{i:04}"
            needs_creation = False
            needs_rebuild = False

            # 检查 agent 是否存在
            if worker_agent_id.lower() not in existing:
                needs_creation = True
            elif initial_setup:
                # 初始设置时，如果存在就跳过
                new_worker_agents.append(worker_agent_id)
                continue
            else:
                # 检查 agent 是否被锁定（通过检查 lock 文件）
                if _is_agent_locked(worker_agent_id):
                    self.logger.warning(
                        f"Worker agent {worker_agent_id} 检测到 lock 文件，正在重建..."
                    )
                    needs_rebuild = True

            # 如果需要创建或重建
            if needs_creation or needs_rebuild:
                if needs_rebuild:
                    # 删除并重建 agent
                    subprocess.run(
                        ["openclaw", "agents", "delete", "--force", worker_agent_id],
                        check=False,
                    )

                if self.create_if_missing and self.model:
                    self.logger.info(f"正在创建 worker agent: {worker_agent_id}")
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
                            break
                else:
                    raise RuntimeError(
                        f"Worker agent {worker_agent_id} 不存在，请设置 model 参数"
                    )

            new_worker_agents.append(worker_agent_id)

        self._worker_agents = new_worker_agents
        if not initial_setup:
            self.logger.info(f"Worker agents 检查完成：{self._worker_agents}")

    def start_serving(self) -> None:
        """启动服务（确保 agent 存在）。"""
        if self._initialized:
            return

        self._ensure_agent()
        self._setup_and_check_worker_agents(initial_setup=True)
        self._initialized = True

    def generate_from_input(
        self,
        user_inputs: List[str],
        system_prompt: str,
        json_schema: dict,
        input_files_data: List[Dict],
    ) -> List[str]:
        """
        生成文本响应（并发执行，每个请求使用独立 worker agent）。

        Args:
            user_inputs: 用户输入列表
            system_prompt: 系统提示词（CLI 模式下不使用）
            json_schema: JSON schema（CLI 模式下不使用）
            input_files_data: 与 user_inputs 长度相同，每个元素是对应 query 的文件内容数据
                             格式：{filename: content_data}

        Returns:
            回复列表，长度与输入相同，失败位置为空字符串
        """
        if not self._initialized:
            self.start_serving()

        # 在每次生成前检查并重建不可用的 worker agents
        self._setup_and_check_worker_agents(initial_setup=False)

        if not user_inputs:
            return []

        if len(input_files_data) != len(user_inputs):
            raise ValueError(
                f"input_files_data 长度 ({len(input_files_data)}) "
                f"必须与 user_inputs 长度 ({len(user_inputs)}) 相同"
            )

        total = len(user_inputs)
        completed = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for i, question in enumerate(user_inputs):
                # 分配 worker agent（轮询）
                worker_agent_id = self._worker_agents[i % len(self._worker_agents)]
                # 传入对应的文件数据
                future = executor.submit(
                    _execute_single_query,
                    worker_agent_id,
                    question,
                    self.timeout,
                    self.logger,
                    input_files_data[i],
                    self.max_retries,
                )
                futures[future] = i

            results: list[str] = [""] * total

            with tqdm(total=total, desc="处理请求", unit="task") as pbar:
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                        completed += 1
                    except Exception as e:
                        self.logger.error(f"[任务 {idx + 1}/{total}] 失败：{e}")
                        results[idx] = ""
                        failed += 1
                    pbar.update(1)

        return results

    def generate_embedding_from_input(self, texts: List[str]) -> List[List[float]]:
        """生成嵌入向量（CLI 模式不支持，返回空向量）。"""
        self.logger.warning("CLI 模式不支持 embedding，返回空向量")
        return [[] for _ in texts]

    def cleanup(self) -> None:
        """清理资源。"""
        self._initialized = False
        self.logger.info("OpenClaw CLI Serving 已清理")


def create_openclaw_serving(
    agent_id: str = "main",
    model: Optional[str] = None,
    max_workers: int = 4,
    max_retries: int = 3,
    **kwargs,
) -> CLIOpenClawServing:
    """
    创建 CLIOpenClawServing 实例的工厂函数。

    Args:
        agent_id: Agent 标识符，默认 "main"
        model: 模型名称
        max_workers: 并发 worker 数量，默认 4
        max_retries: 请求失败时的最大重试次数，默认 3
        **kwargs: 其他参数（timeout, create_if_missing）
    """
    return CLIOpenClawServing(
        agent_id=agent_id,
        model=model,
        max_workers=max_workers,
        max_retries=max_retries,
        **kwargs,
    )
