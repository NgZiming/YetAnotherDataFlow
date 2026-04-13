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
    add_assets_info: bool = True,
) -> list[dict]:
    """
    发送查询到当前 session(OpenClaw 会自动维护 session 上下文)。

    Args:
        agent_id: Agent 标识符
        user_query: 用户查询内容
        timeout: 超时时间 (秒)
        logger: 日志对象
        add_assets_info: 是否添加文件/skills 信息到 prompt

    Returns:
        所有消息列表 (按时间戳排序)

    Raises:
        Exception: 执行失败时抛出异常
    """
    query = user_query
    if add_assets_info:
        assets_dir = _workspace_dir(agent_id) / "assets"
        skills_dir = _workspace_dir(agent_id) / "skills"

        file_paths = []
        skill_paths = []

        if assets_dir.exists():
            for f in assets_dir.iterdir():
                if f.is_file():
                    file_paths.append(str(f))

        if skills_dir.exists():
            for s in skills_dir.iterdir():
                if s.is_dir():
                    skill_paths.append(str(s))

        if file_paths:
            query += f"\n下面是任务相关的文件:\n{file_paths}"
        if skill_paths:
            query += f"\n下面是可用的 skills:\n{skill_paths}"

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
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            d = json.loads(line)
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

        existing = _list_existing_agents()

        if worker_agent_id.lower() not in existing:
            if self.create_if_missing and self.model:
                self.logger.info(f"创建 worker agent: {worker_agent_id}")
                create_agent(worker_agent_id, self.model)

                # 等待 agent 创建完成
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

        return worker_agent_id

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

    def _prepare_execution_context(
        self,
        workspace_path: Path,
        input_files_data: Dict,
        input_skills_data: List[str],
        task_id: Optional[str] = None,
    ) -> Optional[List[str]]:
        """准备执行上下文（清理 workspace、生成文件、创建 session）。"""
        # 使用 task_id 作为 agent_id，确保与 workspace 对应
        if task_id:
            agent_id = self._get_or_create_worker_agent(task_id)
        else:
            # 如果没有 task_id，从 workspace_path 推断
            agent_id = workspace_path.name
            if agent_id == "workspace":
                agent_id = self.agent_id

        assets_dir = workspace_path / "assets"
        skills_dir = workspace_path / "skills"

        new_file_paths: list[str] = []
        new_skill_paths: list[str] = []

        core_files = {
            "AGENTS.md",
            "BOOTSTRAP.md",
            "HEARTBEAT.md",
            "IDENTITY.md",
            "SOUL.md",
            "TOOLS.md",
            "USER.md",
        }

        def _cleanup():
            """清空 assets、skills 目录，并清理 workspace 中非核心文件"""
            for d in [assets_dir, skills_dir]:
                if d.exists():
                    try:
                        shutil.rmtree(d)
                    except Exception as e:
                        self.logger.exception(f"清空 {d} 失败")
                        raise

            if workspace_path.exists():
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

        self.logger.info(f"执行前清理 workspace: {workspace_path}")
        _cleanup()

        # 生成文件到 assets 目录
        if input_files_data:
            assets_dir.mkdir(parents=True, exist_ok=True)
            for filename, content_data in input_files_data.items():
                if not content_data or not isinstance(content_data, dict):
                    continue
                new_path = assets_dir / Path(filename).name
                from dataflow.utils.generate_binary_files import generate_file

                generate_file(
                    {"filename": new_path.name, "content": content_data},
                    str(assets_dir),
                )
                new_file_paths.append(str(new_path))

        # 拷贝 skill 目录
        if input_skills_data and self.skill_base_dir:
            skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_name in input_skills_data:
                if Path(skill_name).is_absolute():
                    src_path = Path(skill_name)
                else:
                    src_path = Path(self.skill_base_dir) / skill_name

                if not src_path.exists() or not src_path.is_dir():
                    raise Exception(f"Skill 路径不存在：{src_path}")

                dst_path = skills_dir / src_path.name
                shutil.copytree(src_path, dst_path)
                new_skill_paths.append(str(dst_path))

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

        return [Path(p).name for p in new_file_paths]

    def _cleanup_execution_context(self, workspace_path: Path, task_id: Optional[str] = None) -> None:
        """清理执行上下文资源。"""
        # 使用 task_id 作为 agent_id，确保与 prepare 一致
        if task_id:
            agent_id = self._get_or_create_worker_agent(task_id)
        else:
            agent_id = workspace_path.name
            if agent_id == "workspace":
                agent_id = self.agent_id

        assets_dir = workspace_path / "assets"
        skills_dir = workspace_path / "skills"

        core_files = {
            "AGENTS.md",
            "BOOTSTRAP.md",
            "HEARTBEAT.md",
            "IDENTITY.md",
            "SOUL.md",
            "TOOLS.md",
            "USER.md",
        }

        # 清空 assets、skills 目录，并清理 workspace 中非核心文件
        for d in [assets_dir, skills_dir]:
            if d.exists():
                try:
                    shutil.rmtree(d)
                    self.logger.debug(f"清理目录：{d}")
                except Exception as e:
                    self.logger.exception(f"清空 {d} 失败")
                    raise

        if workspace_path.exists():
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

    def _send_query(
        self,
        workspace_path: Path,
        query: str,
        add_assets_info: bool = False,
        current_time: Optional[str] = None,
    ) -> TrajectoryDict:
        """
        发送查询并获取响应（返回标准轨迹字典）。

        Args:
            workspace_path: workspace 路径
            query: 用户查询
            add_assets_info: 是否添加文件信息到查询
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
        agent_id = workspace_path.name
        if agent_id == "workspace":
            agent_id = self.agent_id

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
        }

        messages = _send_query_to_session(
            agent_id,
            query,
            self.timeout,
            self.logger,
            add_assets_info=add_assets_info,
        )

        # 将 system 消息插入到开头
        messages.insert(0, system_message)

        # 提取最终输出
        output = ""
        for m in reversed(messages):
            if m.get("message", {}).get("role", None) == "assistant":
                content_list = m.get("message", {}).get("content", [])
                content_parts = []
                for item in content_list:
                    if isinstance(item, dict) and item.get("type") == "text":
                        content_parts.append(item.get("text", ""))
                output = "\n\n".join(content_parts)
                break

        if not output:
            self.logger.warning("未找到助手消息")
            raise Exception("未找到助手消息")

        # 返回标准轨迹字典（子类内部格式化）
        return {
            "messages": messages,
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
