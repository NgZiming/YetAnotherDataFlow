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
import shutil
import subprocess
import time

from pathlib import Path
from typing import Optional, Dict, Any, List

from dataflow.logger import get_logger

# 导入 AgentServingABC 基类
from .iface import AgentServingABC, TrajectoryDict
from .system_prompt_builder import build_system_prompt

# OpenClaw 基础目录
OPENCLAW_BASE = Path.home() / ".openclaw"


def _read_skills_info(skills_dir: Path) -> list[dict[str, str]]:
    """
    从 skills 目录读取 skill 信息（name, description, location）

    Args:
        skills_dir: skills 目录路径

    Returns:
        skill 信息列表
    """
    skills_info = []

    if not skills_dir.exists():
        return skills_info

    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue

        skill_md = skill_dir / "SKILL.md"
        description = ""

        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8")
                for line in content.splitlines():
                    if line.strip().startswith("<description>"):
                        start = line.find("<description>")
                        end = line.find("</description>")
                        if start >= 0 and end > start:
                            description = line[start + 13 : end].strip()
                            break
            except Exception:
                pass

        skills_info.append(
            {
                "name": skill_dir.name,
                "description": description,
                "location": str(skill_dir),
            }
        )

    return skills_info


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


def _wait_for_lock_release(session_file: Path, timeout: int = 30) -> None:
    """
    等待 session 文件的 lock 文件释放。

    OpenClaw 在写入 session 文件时会创建对应的 .lock 文件，
    写入完成后会删除 lock 文件。需要等待 lock 文件释放后再读取。

    Args:
        agent_id: Agent 标识符
        session_file: session 文件路径
        timeout: 超时时间（秒）

    Raises:
        TimeoutError: 超时后 lock 文件仍未释放
    """
    lock_file = session_file.with_suffix(session_file.suffix + ".lock")

    if not lock_file.exists():
        # 没有 lock 文件，直接返回
        return

    start_time = time.time()
    logger = get_logger()
    logger.info(f"等待 lock 文件释放：{lock_file}")

    while time.time() - start_time < timeout:
        if not lock_file.exists():
            logger.info(f"Lock 文件已释放：{lock_file}")
            return
        time.sleep(0.5)

    # 超时后尝试读取，可能文件已经写完但 lock 未清理
    logger.warning(f"Lock 文件超时（{timeout}秒），尝试继续：{lock_file}")


def _resolve_transcript_paths(agent_id: str) -> List[Path]:
    """
    解析 sessions 文件夹下所有的 jsonl 文件路径。

    Args:
        agent_id: Agent 标识符

    Returns:
        所有 jsonl 文件路径列表（按修改时间排序，最新的在前）

    Raises:
        FileNotFoundError: 超时后文件仍未找到
    """
    agent_dir = _agent_store_dir(agent_id)
    sessions_dir = agent_dir / "sessions"

    start_time = time.time()
    timeout = 60

    while time.time() - start_time < timeout:
        if sessions_dir.exists():
            candidates = list(sessions_dir.rglob("*.jsonl"))
            if candidates:
                return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)

        time.sleep(5.0)

    raise FileNotFoundError(f"Agent {agent_id} 的 session 文件在 {timeout} 秒内未生成")


def _send_query_to_session(
    agent_id: str,
    user_query: str,
    timeout: int,
    logger: Any,
) -> list[dict]:
    """
    发送查询到当前 session(OpenClaw 会自动维护 session 上下文)。

    Args:
        agent_id: Agent 标识符
        user_query: 用户查询内容
        timeout: 超时时间 (秒)
        logger: 日志对象

    Returns:
        所有消息列表 (按时间戳排序)

    Raises:
        Exception: 执行失败时抛出异常
    """
    # 验证 user_query 不为空
    if not user_query or not str(user_query).strip():
        raise ValueError("user_query 不能为空")

    query = str(user_query).strip()

    logger.info(
        f"向 agent {agent_id} 发送查询 (长度={len(query)}字符): {query[:100]}..."
    )

    cmd = ["openclaw", "agent", "--agent", agent_id, "--local", "-m", query]

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise Exception(result.stderr)

    logger.info(f"请求完成:{result.stdout[:50]}...")

    transcript_paths = _resolve_transcript_paths(agent_id)
    messages = []
    for transcript_path in transcript_paths:
        # 等待 lock 文件释放
        _wait_for_lock_release(transcript_path, timeout=30)

        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning(f"解析 JSON 失败 {transcript_path.name}: {e}")
                continue
            d["session_file"] = transcript_path.name
            messages.append(d)
    messages = sorted(messages, key=lambda x: x["timestamp"])

    return messages


# ============================================================================
# CLIOpenClawServing 类
# ============================================================================


class CLIOpenClawServing(AgentServingABC):
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
        timeout: int = 1200,
        create_if_missing: bool = True,
        max_workers: int = 4,
        max_retries: int = 3,
        skill_base_dir: Optional[str] = None,
        verification_api_key: Optional[str] = None,
        verification_base_url: Optional[str] = None,
        verification_client_params: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化 CLIOpenClawServing。

        Args:
            agent_id: Agent 标识符，默认 "main"
            model: 模型名称，如果 agent 不存在且 create_if_missing=True 则用于创建
            timeout: 单次查询超时时间（秒）
            create_if_missing: 如果 agent 不存在是否自动创建
            max_workers: 并发 worker 数量
            max_retries: 请求失败时的最大重试次数
            skill_base_dir: Skill 基础目录
            verification_api_key: 验证用 API key
            verification_base_url: 验证用 API base URL
            verification_client_params: 验证用 LLM 调用参数
        """
        self.logger = get_logger()

        self.agent_id = agent_id
        self.model: str = model or "vllm//data/share/models/Qwen3.5-122B-A10B/"
        self.timeout = timeout
        self.create_if_missing = create_if_missing
        self.skill_base_dir = skill_base_dir

        super().__init__(
            max_workers=max_workers,
            max_retries=max_retries,
            timeout=timeout,
            verification_api_url=verification_base_url,
            verification_api_key=verification_api_key,
            verification_client_params=verification_client_params,
        )

        self._initialized = False
        # 不再预先创建 worker agents，改为动态管理
        # self._worker_agents: List[str] = []

        self.logger.info(
            f"CLIOpenClawServing 初始化：agent_id={agent_id}, model={self.model}"
        )

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

    def _get_or_create_worker_agent(self, task_id: str) -> str:
        """
        为任务获取或创建 worker agent。

        Args:
            task_id: 任务 ID（如 "task-0"）

        Returns:
            worker agent ID（与 task_id 一致，确保 workspace 匹配）
        """
        # 直接用 task_id 作为 agent_id，确保 workspace 路径和 agent 一致
        worker_agent_id = task_id

        max_attempts = 20  # 最多等待 200 秒
        retry_delay = 10.0  # 每次重试间隔

        for attempt in range(max_attempts):
            existing = _list_existing_agents()

            if worker_agent_id.lower() in existing:
                self.logger.debug(f"Worker agent 已存在：{worker_agent_id}")
                return worker_agent_id

            # agent 不存在，尝试创建
            if self.create_if_missing and self.model:
                self.logger.info(
                    f"尝试创建 worker agent: {worker_agent_id} (attempt {attempt + 1}/{max_attempts})"
                )
                try:
                    create_agent(worker_agent_id, self.model)
                except RuntimeError as e:
                    error_msg = str(e)
                    # 如果是因为配置冲突，等待后重试
                    if (
                        "ConfigMutationConflictError" in error_msg
                        or "config changed" in error_msg
                    ):
                        self.logger.warning(f"配置冲突，等待后重试：{e}")
                        time.sleep(retry_delay)
                        continue
                    # 其他错误直接抛出
                    raise

                # 等待 agent 创建完成并注册
                self.logger.debug(f"等待 agent 注册：{worker_agent_id}")
                time.sleep(retry_delay)
            else:
                raise RuntimeError(
                    f"Worker agent {worker_agent_id} 不存在，请设置 model 参数"
                )

        # 所有尝试都失败后
        raise RuntimeError(
            f"创建 worker agent {worker_agent_id} 超时（等待了 {max_attempts * retry_delay} 秒）"
        )

    def start_serving(self) -> None:
        """启动服务（确保 agent 存在）。"""
        if self._initialized:
            return

        self._ensure_agent()
        self._initialized = True

    # =========================================================================
    # AgentServingABC 抽象方法实现
    # =========================================================================

    def _get_workspace_path(self, task_id: str) -> Path:
        """获取任务的 workspace 路径。"""
        # 使用 task_id 作为 workspace 目录名，保持与 agent_id 一致
        return _workspace_dir(task_id)

    def _cleanup(self, workspace_path: Path):
        core_files = {
            "AGENTS.md",
            "BOOTSTRAP.md",
            "HEARTBEAT.md",
            "IDENTITY.md",
            "SOUL.md",
            "TOOLS.md",
            "USER.md",
        }
        for item in workspace_path.iterdir():
            if item.name in core_files:
                continue
            if item.is_file():
                try:
                    item.unlink()
                    self.logger.debug(f"清理 workspace 文件：{item.name}")
                except Exception as e:
                    self.logger.exception(f"清理文件失败 {item}")
                    raise
            elif item.is_dir():
                try:
                    shutil.rmtree(item)
                    self.logger.debug(f"清理 workspace 目录：{item.name}")
                except Exception as e:
                    self.logger.exception(f"清理目录失败 {item}")
                    raise

    def _prepare_execution_context(
        self,
        workspace_path: Path,
        input_files_data: Dict,
        input_skills_data: List[str],
        task_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """准备执行上下文（清理 workspace、生成文件、创建 session）。"""
        # 使用 task_id 作为 agent_id，确保与 workspace 对应
        if task_id:
            agent_id = self._get_or_create_worker_agent(task_id)
        else:
            # 如果没有 task_id，从 workspace_path 推断
            agent_id = workspace_path.name
            if agent_id == "workspace":
                agent_id = self.agent_id

        self.logger.info(f"执行前清理 workspace: {workspace_path}")
        self._cleanup(workspace_path)

        # 使用父类的 _prepare_files 方法生成文件和 skills
        path_mapping = self._prepare_files(
            workspace_path,
            input_files_data,
            input_skills_data,
            self.skill_base_dir,
        )

        # 执行 /new 创建新 session
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
            timeout=self.timeout,
            check=False,
        )
        if new_result.returncode != 0:
            raise RuntimeError(f"/new 命令失败：{new_result.stderr}")

        return path_mapping

    def _cleanup_execution_context(
        self, workspace_path: Path, task_id: Optional[str] = None
    ) -> None:
        """清理执行上下文资源。"""
        # 使用 task_id 作为 agent_id，确保与 prepare 一致
        if task_id:
            agent_id = self._get_or_create_worker_agent(task_id)
        else:
            agent_id = workspace_path.name
            if agent_id == "workspace":
                agent_id = self.agent_id

        self._cleanup(workspace_path)

    def _send_query(
        self,
        workspace_path: Path,
        query: str,
        current_time: Optional[str] = None,
    ) -> TrajectoryDict:
        """
        发送查询并获取响应（返回标准轨迹字典）。

        Args:
            workspace_path: workspace 路径
            query: 用户查询
            current_time: 当前时间字符串，如果为 None 则使用默认时间

        Returns:
            标准轨迹字典 (AgentServingABC.TrajectoryDict):
            {
                "messages": List[AgentServingABC.MessageDict],
                "final_output": str,
                "files_created": List[str],
                "errors": List[str],
            }
        """
        # 验证 query 不为空
        if not query or not str(query).strip():
            self.logger.error(
                f"_send_query: query 为空！workspace_path={workspace_path}"
            )
            raise ValueError("query 不能为空")

        agent_id = workspace_path.name
        if agent_id == "workspace":
            agent_id = self.agent_id

        # 检查 agent 是否存在
        existing = _list_existing_agents()
        if agent_id.lower() not in existing:
            self.logger.error(
                f"Agent {agent_id} 不存在，请确保 agent 已创建。可用 agents: {existing}"
            )
            raise ValueError(f"Agent {agent_id} 不存在")

        # session_id 从 session_file 中提取 UUID
        session_id = None

        # 构建 system prompt
        skills_dir = workspace_path / "skills"
        skills_info = _read_skills_info(skills_dir)
        system_prompt = build_system_prompt(
            workspace_path=str(workspace_path),
            skills=skills_info,
            current_time=current_time,
        )

        # 在消息列表开头插入 system 消息
        system_message = {
            "round": 0,
            "role": "system",
            "content": system_prompt,
            "thought": None,
            "tool_calls": [],
            "tool_results": [],
            "id": None,
            "parentId": None,
            "session_id": session_id,
        }

        messages = _send_query_to_session(
            agent_id,
            query,
            self.timeout,
            self.logger,
        )

        # 为每条消息添加 session_id 并统一格式
        formatted_messages = []
        user_round = 0
        assistant_round = 0
        has_system_message = False

        for m in messages:
            # 从 session_file 中提取该消息对应的 session_id
            session_file = m.get("session_file", "")
            if session_file:
                if session_file.endswith(".jsonl"):
                    m["session_id"] = session_file[:-6]
                else:
                    m["session_id"] = session_file
            elif "session_id" not in m:
                # fallback: 使用 agent_id
                m["session_id"] = agent_id

            # 如果是原始 transcript 格式，转换为 MessageDict 格式
            if m.get("type") == "message":
                inner_msg = m.get("message", {})
                role = inner_msg.get("role", "unknown")

                # 计算 round 号
                if role == "user":
                    user_round += 1
                    round_num = user_round
                elif role == "assistant":
                    assistant_round += 1
                    round_num = assistant_round
                else:
                    round_num = 0

                formatted_msg = {
                    "round": round_num,
                    "role": role,
                    "content": inner_msg.get("content", []),
                    "thought": None,
                    "tool_calls": [],
                    "tool_results": [],
                    "id": m.get("id"),
                    "parentId": m.get("parentId"),
                    "session_id": m["session_id"],
                }

                # 处理 toolResult
                if role == "toolResult":
                    formatted_msg["tool_results"] = [
                        {
                            "toolCallId": inner_msg.get("toolCallId"),
                            "toolName": inner_msg.get("toolName"),
                            "content": inner_msg.get("content", []),
                        }
                    ]

                # 处理 toolCall 和 thinking
                if role == "assistant":
                    content_list = inner_msg.get("content", [])
                    for item in content_list:
                        if isinstance(item, dict):
                            if item.get("type") == "toolCall":
                                formatted_msg["tool_calls"].append(
                                    {
                                        "id": item.get("id"),
                                        "name": item.get("name"),
                                        "arguments": item.get("arguments"),
                                    }
                                )
                            elif item.get("type") == "thinking":
                                formatted_msg["thought"] = item.get("thinking")

                formatted_messages.append(formatted_msg)
            elif m.get("type") == "system":
                # system 消息已经格式化过了，标记已存在
                has_system_message = True
                formatted_messages.append(m)
            else:
                # 其他类型（session, model_change 等）跳过
                pass

        # 只有当 transcript 中没有 system 消息时，才插入新的 system message
        if not has_system_message:
            formatted_messages.insert(0, system_message)
        messages.insert(0, system_message)

        # 提取最终输出（从最后一条 role=assistant 的消息中提取 text 内容）
        output = ""
        for m in reversed(formatted_messages):
            if m.get("role") == "assistant":
                content_list = m.get("content", [])
                content_parts = []
                for item in content_list:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            content_parts.append(item.get("text", ""))
                        elif item.get("type") == "thinking":
                            # thinking 不算作最终输出
                            pass
                    elif isinstance(item, str):
                        content_parts.append(item)
                output = "\n\n".join(content_parts)
                # 只要有 text 内容就停止，找最后一条有实际输出的 assistant 消息
                if output:
                    break

        if not output:
            self.logger.warning("未找到助手消息")
            raise Exception("未找到助手消息")

        # 返回标准轨迹字典（子类内部格式化）
        return {
            "messages": formatted_messages,
            "final_output": output,
            "files_created": [],
            "errors": [],
        }

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
    """
    return CLIOpenClawServing(
        agent_id=agent_id,
        model=model,
        max_workers=max_workers,
        max_retries=max_retries,
        **kwargs,
    )
