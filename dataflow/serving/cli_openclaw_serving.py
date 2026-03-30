"""
OpenClaw Serving via CLI (基于 openclaw CLI 命令，支持并发)

通过 openclaw CLI 命令执行任务，每个请求在独立的 session 中运行，
使用线程池并发处理多个请求。

特点:
- 不使用 WebSocket API，直接调用 openclaw CLI
- 每个请求在 agent 的不同 session 中执行
- 支持并发处理，每次调用创建临时线程池
- 对象可 pickle 拷贝（适合分布式任务）

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
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from dataflow.core import LLMServingABC
from dataflow.logger import get_logger

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
        env = os.environ.copy()
        if "/root/.nvm/versions/node/v24.14.1/bin/" not in env["PATH"]:
            env["PATH"] = "/root/.nvm/versions/node/v24.14.1/bin/:" + env["PATH"]
        result = subprocess.run(
            ["openclaw", "agents", "list"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
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
    env = os.environ.copy()
    if "/root/.nvm/versions/node/v24.14.1/bin/" not in env["PATH"]:
        env["PATH"] = "/root/.nvm/versions/node/v24.14.1/bin/:" + env["PATH"]
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
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create agent {agent_id}: {result.stderr.strip()}"
        )


# ============================================================================
# Session 管理函数
# ============================================================================


def _resolve_transcript_path(agent_id: str) -> Optional[Path]:
    """
    解析新生成的 session 文件路径。

    通过两种方式查找：
    1. 优先通过 sessions.json 索引查找最新的 session
    2. fallback 到查找最新修改的 .jsonl/.ndjson 文件

    Args:
        agent_id: Agent 标识符

    Returns:
        Session 文件路径，如果找不到则返回 None
    """
    agent_dir = _agent_store_dir(agent_id)
    sessions_dir = agent_dir / "sessions"

    for attempt in range(15):
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

        if attempt < 14:
            time.sleep(1.0)

    return None


def load_session(agent_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    加载 session 文件并解析为消息列表。

    Args:
        agent_id: Agent 标识符

    Returns:
        Session 消息列表，如果加载失败则返回 None
    """
    transcript_path = _resolve_transcript_path(agent_id)
    if transcript_path is None:
        return None

    messages = []
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return messages if messages else None


# ============================================================================
# 响应处理函数
# ============================================================================


def _execute_single_query(
    agent_id: str,
    user_query: str,
    session_id: str,
    timeout: int,
    logger: Any,
) -> str:
    """
    执行单个查询请求。

    Args:
        agent_id: Agent 标识符
        user_query: 用户查询内容
        session_id: Session 标识符（UUID）
        timeout: 超时时间（秒）
        logger: 日志对象

    Returns:
        助手的回复文本，如果执行失败则返回空字符串
    """
    workspace = _workspace_dir(agent_id)

    try:
        env = os.environ.copy()
        if "/root/.nvm/versions/node/v24.14.1/bin/" not in env["PATH"]:
            env["PATH"] = "/root/.nvm/versions/node/v24.14.1/bin/:" + env["PATH"]
        cmd = [
            "openclaw",
            "agent",
            "--agent",
            agent_id,
            "--session-id",
            session_id,
            "--message",
            shlex.quote(user_query),
        ]
        logger.info(" ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=timeout,
            check=False,
            env=env,
        )
        # 检查执行是否成功（0 或 -1 表示正常，-1 可能是被信号中断）
        if result.returncode not in (0, -1):
            logger.warning(f"openclaw agent 退出码 {result.returncode}")
            raise Exception(result.stderr)
    except subprocess.TimeoutExpired:
        logger.warning(f"查询超时 ({timeout}s)")
        return ""
    except Exception:
        logger.exception("查询失败")
        raise

    messages = load_session(agent_id)
    return json.dumps(messages, ensure_ascii=False) if messages else ""


# ============================================================================
# CLIOpenClawServing 类
# ============================================================================


class CLIOpenClawServing(LLMServingABC):
    """
    通过 CLI 执行 OpenClaw 任务的 Serving 类。

    特点:
    - 使用 openclaw CLI 命令执行任务，不依赖 WebSocket API
    - 每个请求在独立的 session 中运行
    - 支持并发处理，每次 generate_from_input 调用创建临时线程池
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
        self.model = model
        self.timeout = timeout
        self.create_if_missing = create_if_missing
        self.max_workers = max_workers
        self._initialized = False

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

    def start_serving(self) -> None:
        """启动服务（确保 agent 存在）。"""
        if self._initialized:
            return

        self._ensure_agent()
        self._initialized = True
        self.logger.info(
            f"OpenClaw CLI Serving 已就绪 "
            f"(agent={self.agent_id}, max_workers={self.max_workers}, timeout={self.timeout}s)"
        )

    def generate_from_input(
        self,
        user_inputs: List[str],
        system_prompt: str,
        json_schema: dict,
    ) -> List[str]:
        """
        生成文本响应（并发执行）。

        Args:
            user_inputs: 用户输入列表
            system_prompt: 系统提示词（CLI 模式下不使用）
            json_schema: JSON schema（CLI 模式下不使用）

        Returns:
            回复列表，长度与输入相同，失败位置为空字符串
        """
        if not self._initialized:
            self.start_serving()

        if not user_inputs:
            return []

        self.logger.info(
            f"处理 {len(user_inputs)} 个请求 (workers={self.max_workers})..."
        )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for i, question in enumerate(user_inputs):
                session_id = str(uuid.uuid4())
                future = executor.submit(
                    _execute_single_query,
                    self.agent_id,
                    question,
                    session_id,
                    self.timeout,
                    self.logger,  # 传递 logger 用于记录错误
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
