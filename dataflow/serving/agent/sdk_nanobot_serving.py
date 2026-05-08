"""
SDKNanobotServing - 基于 nanobot Python SDK 的轻量级 Serving 类

替代 CLIOpenClawServing，纯 Python 实现，无 CLI 依赖。

注意：请设置环境变量 NANOBOT_MAX_CONCURRENT_REQUESTS，来提高并发

特点:
- 纯 Python 调用，无 subprocess 开销
- 自动创建配置文件，无需 CLI
- session_key 实现会话隔离
- 支持 async 原生并发
- 每个请求独立临时目录，避免并发冲突
- 支持外部技能目录（通过符号链接）

使用示例:
    from dataflow.serving import SDKNanobotServing

    serving = SDKNanobotServing(
        model="/data/share/models/Qwen3.5-122B-A10B/",
        max_workers=4,
    )
    responses = serving.generate_from_input(["问题 1", "问题 2"])
"""

from __future__ import annotations

import asyncio
import json
import uuid
import shutil
import time
import platform

from pathlib import Path
from typing import List, Dict, Optional, Any

from dataflow.logger import get_logger

# 导入 Nanobot 内部组件用于构建高保真 System Prompt
from nanobot.utils.prompt_templates import render_template
from nanobot.agent.skills import SkillsLoader

# 导入 AgentServingABC 基类
from dataflow.core.agentic import AgentServingABC, TrajectoryDict, UserSimulatorABC


class SDKNanobotServing(AgentServingABC):
    """
    基于 nanobot Python SDK 的轻量级 Serving 类。

    特点:
    - 纯 Python 调用，无 subprocess 开销
    - 自动创建配置文件，无需 CLI
    - session_key 实现会话隔离
    - 支持 async 原生并发
    - 每个请求独立临时目录，避免并发冲突
    - 支持外部技能目录（通过符号链接）
    """

    def __init__(
        self,
        user: UserSimulatorABC,
        config_path: str = "~/.nanobot/config.json",
        workspace: str = "~/.nanobot/workspace",
        model: Optional[str] = None,
        provider: str = "vllm",
        api_base: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        max_workers: int = 4,
        max_retries: int = 3,
        auto_create_config: bool = True,
        extra_skills_dirs: Optional[List[str]] = None,
        user_md: Optional[str] = None,
        soul_md: Optional[str] = None,
    ):
        """
        初始化 SDKNanobotServing。

        Args:
            config_path: 配置文件路径
            workspace: 工作目录
            model: 模型名称
            provider: LLM Provider
            max_workers: 并发数
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
            auto_create_config: 是否自动创建配置
            extra_skills_dirs: 额外技能目录列表
            verification_api_url: 验证用 API URL
            verification_api_key: 验证用 API Key
            verification_client_params: 验证用 LLM 参数
        """
        self.logger = get_logger()

        self.config_path = Path(config_path).expanduser()
        self.workspace = Path(workspace).expanduser()
        self.model = model or "/data/share/models/Qwen3.5-122B-A10B/"
        self.provider = provider
        self.api_base = api_base
        self.api_key = api_key
        self.extra_skills_dirs = [
            Path(d).expanduser() for d in (extra_skills_dirs or [])
        ]
        self.user_md = user_md
        self.soul_md = soul_md

        super().__init__(
            user=user,
            max_workers=max_workers,
            max_retries=max_retries,
        )

        self._initialized = False
        self.bot = None

        # 自动创建/更新配置
        if auto_create_config:
            self._create_config(force=True)

        # 链接外部技能目录
        self._link_extra_skills()

        # 清理旧的临时目录
        self._cleanup_old_temp_dirs()

        # 加载 nanobot
        self._load_nanobot()

        self.logger.info(
            f"SDKNanobotServing 初始化：workspace={self.workspace}, model={self.model}"
        )

    def _cleanup_old_temp_dirs(self, max_age_hours: int = 1) -> None:
        """清理超过指定时间的临时目录"""
        temp_base = self.workspace / ".temp"
        if not temp_base.exists():
            return

        current_time = time.time()
        max_age_seconds = max_age_hours * 3600

        try:
            for temp_dir in temp_base.iterdir():
                if temp_dir.is_dir():
                    mtime = temp_dir.stat().st_mtime
                    if current_time - mtime > max_age_seconds:
                        shutil.rmtree(temp_dir)
                        self.logger.debug(f"清理旧临时目录：{temp_dir}")
        except Exception as e:
            self.logger.warning(f"清理临时目录失败：{e}")

    def _link_extra_skills(self) -> None:
        """将额外技能目录符号链接到 workspace/skills"""
        if not self.extra_skills_dirs:
            return

        workspace_skills = self.workspace / "skills"
        workspace_skills.mkdir(parents=True, exist_ok=True)

        existing_skills = {p.name for p in workspace_skills.iterdir() if p.exists()}

        linked_count = 0
        skipped_count = 0

        for extra_dir in self.extra_skills_dirs:
            if not extra_dir.exists():
                self.logger.warning(f"额外技能目录不存在，跳过：{extra_dir}")
                continue

            skills_to_link = []
            for skill_subdir in extra_dir.iterdir():
                if not skill_subdir.is_dir():
                    continue

                skill_name = skill_subdir.name
                if skill_name in existing_skills:
                    skipped_count += 1
                    continue

                skills_to_link.append((skill_name, skill_subdir))
                existing_skills.add(skill_name)

            for skill_name, skill_subdir in skills_to_link:
                target_link = workspace_skills / skill_name
                try:
                    target_link.symlink_to(skill_subdir, target_is_directory=True)
                    linked_count += 1
                except Exception as e:
                    self.logger.error(f"链接技能失败 {skill_name}: {e}")

        if linked_count > 0 or skipped_count > 0:
            self.logger.info(
                f"技能链接完成：{linked_count} 个新链接，{skipped_count} 个已存在"
            )

    def _create_config(self, force: bool = False) -> None:
        """创建配置文件"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

        config_data: Dict[str, Any] = {
            "agents": {
                "defaults": {
                    "workspace": str(self.workspace),
                    "model": self.model,
                    "provider": self.provider,
                    "timezone": "Asia/Shanghai",
                    "maxTokens": 8192,
                    "contextWindowTokens": 131072,
                    "temperature": 0.1,
                    "dream": {"intervalH": 2},
                }
            },
            "providers": {
                self.provider: {"apiKey": self.api_key, "apiBase": self.api_base}
            },
            "tools": {
                "web": {"enable": True, "search": {"provider": "duckduckgo"}},
                "exec": {"enable": True, "timeout": 60},
                "restrictToWorkspace": False,
            },
            "gateway": {"heartbeat": {"enabled": False}},
        }

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Nanobot 配置已创建：{self.config_path}")

    def _load_nanobot(self) -> None:
        """加载 nanobot 实例"""
        try:
            from nanobot import Nanobot

            self.logger.info(
                f"加载 Nanobot: config_path={self.config_path}, workspace={self.workspace}"
            )

            self.bot = Nanobot.from_config(
                config_path=str(self.config_path), workspace=str(self.workspace)
            )
            self.logger.info("Nanobot 实例已加载")
        except Exception as e:
            self.logger.error(f"加载 Nanobot 失败：{e}")
            raise

    def _build_full_system_prompt(self, workspace_path: Path) -> str:
        """
        镜像 Nanobot 的 ContextBuilder.build_system_prompt 逻辑，
        用于在轨迹中补全高保真的 System Prompt。
        """
        parts = []

        # 1. Identity
        try:
            system = platform.system()
            runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
            identity = render_template(
                "agent/identity.md",
                workspace_path=str(workspace_path.expanduser().resolve()),
                runtime=runtime,
                platform_policy=render_template(
                    "agent/platform_policy.md", system=system
                ),
                channel="cli",
            )
            parts.append(identity)
        except Exception as e:
            self.logger.warning(f"构建 Identity 失败: {e}")

        # 2. Bootstrap Files (AGENTS.md, SOUL.md, USER.md, TOOLS.md)
        bootstrap_files = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
        bootstrap_parts = []
        for filename in bootstrap_files:
            file_path = workspace_path / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                bootstrap_parts.append(f"## {filename}\n\n{content}")
        if bootstrap_parts:
            parts.append("\n\n".join(bootstrap_parts))

        # 3. Skills Summary
        try:
            loader = SkillsLoader(workspace_path)
            skills_summary = loader.build_skills_summary()
            if skills_summary:
                parts.append(f"# Available Skills\n\n{skills_summary}")
        except Exception as e:
            self.logger.warning(f"构建 Skills Summary 失败: {e}")

        return "\n\n---\n\n".join(parts)

    # =========================================================================
    # Trajectory Capture Hook
    # =========================================================================

    class TrajectoryCaptureHook:
        """
        Custom hook to capture full agent trajectory in memory.
        Implements the AgentHook interface required by nanobot.
        """

        def __init__(self) -> None:
            self.trajectory: List[Dict[str, Any]] = []

        def wants_streaming(self) -> bool:
            return False

        async def after_iteration(self, context: Any) -> None:
            # context is AgentHookContext
            iteration = context.iteration

            thought = None
            if context.response:
                thought = context.response.content

            tool_calls = []
            for tc in context.tool_calls:
                tool_calls.append({"name": tc.name, "arguments": tc.arguments})

            tool_results = []
            for tr in context.tool_results:
                tool_results.append(str(tr))

            self.trajectory.append(
                {
                    "iteration": iteration,
                    "thought": thought,
                    "tool_calls": tool_calls,
                    "tool_results": tool_results,
                    "messages": list(context.messages),
                }
            )

    def _get_workspace_path(self, task_id: str) -> Path:
        """获取任务的 workspace 路径（临时目录）。"""
        temp_base = self.workspace / ".temp"
        temp_base.mkdir(parents=True, exist_ok=True)
        return temp_base / f"{task_id}_{uuid.uuid4().hex[:8]}"

    def _prepare_execution_context(
        self,
        workspace_path: Path,
        input_files_data: Dict,
        input_skills_data: List[str],
        task_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """准备执行上下文（创建临时目录、生成文件、注入性格）。"""
        workspace_path.mkdir(parents=True, exist_ok=True)

        # 1. 性格注入: Nanobot ContextBuilder 自动加载根目录的 USER.md 和 SOUL.md
        if self.user_md:
            (workspace_path / "USER.md").write_text(self.user_md, encoding="utf-8")
        if self.soul_md:
            (workspace_path / "SOUL.md").write_text(self.soul_md, encoding="utf-8")

        # 2. 使用基类方法准备文件，确保路径映射和目录结构正确
        path_mapping = self._prepare_files(
            workspace_path=workspace_path,
            input_files_data=input_files_data,
            input_skills_data=[],  # 技能由下面的逻辑处理
            skill_base_dir="",
        )

        # 3. 链接技能目录
        if input_skills_data and self.extra_skills_dirs:
            skills_dir = workspace_path / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_name in input_skills_data:
                for extra_dir in self.extra_skills_dirs:
                    src_path = extra_dir / skill_name
                    if src_path.exists() and src_path.is_dir():
                        dst_path = skills_dir / skill_name
                        if not dst_path.exists():
                            dst_path.symlink_to(src_path, target_is_directory=True)
                        break

        return path_mapping

    def _cleanup_execution_context(
        self, workspace_path: Path, task_id: Optional[str] = None
    ) -> None:
        """清理执行上下文资源（删除临时目录）。"""
        if workspace_path.exists():
            try:
                shutil.rmtree(workspace_path)
                self.logger.debug(f"清理临时目录：{workspace_path}")
            except Exception as e:
                self.logger.error(f"清理临时目录失败 {workspace_path}: {e}")

    def _send_query(
        self,
        workspace_path: Path,
        query: str,
        current_time: Optional[str] = None,
    ) -> TrajectoryDict:
        """
        发送查询并获取响应（通过内存 Hook 实时捕获完整轨迹）。
        """
        # 使用 workspace_path 的哈希作为 session_key，确保同一对话在多轮验证中共享 session
        import hashlib

        session_key = hashlib.sha256(str(workspace_path).encode()).hexdigest()[:16]

        # 初始化轨迹捕获 Hook
        capture_hook = self.TrajectoryCaptureHook()

        # 执行异步查询
        async def run_query():
            # 注入自定义 Hook 以捕获详细轨迹
            return await self.bot.run(
                query,
                session_key=session_key,
                hooks=[capture_hook],
            )

        try:
            result = asyncio.run(run_query())
        except Exception as e:
            self.logger.error(f"Nanobot 运行失败: {e}")
            raise e

        content = result.content if result.content else ""

        # 从 Hook 的内存记录中构建标准轨迹字典
        formatted_messages = []

        # --- 补全 System Prompt (高保真镜像 Nanobot 逻辑) ---
        try:
            system_prompt = self._build_full_system_prompt(workspace_path)
            if system_prompt:
                formatted_messages.append(
                    {
                        "round": 0,
                        "role": "system",
                        "content": system_prompt,
                        "thought": None,
                        "tool_calls": [],
                        "tool_results": [],
                        "id": None,
                        "parentId": None,
                        "session_id": session_key,
                    }
                )
        except Exception as e:
            self.logger.warning(f"轨迹 System Prompt 补全失败: {e}")
        # -------------------------------------------------
        for i, step in enumerate(capture_hook.trajectory):
            if step["thought"]:
                formatted_messages.append(
                    {
                        "round": i + 1,
                        "role": "assistant",
                        "content": "",
                        "thought": step["thought"],
                        "tool_calls": step["tool_calls"],
                        "tool_results": [],
                        "id": None,
                        "parentId": None,
                        "session_id": session_key,
                    }
                )

            if step["tool_results"]:
                formatted_messages.append(
                    {
                        "round": i + 1,
                        "role": "tool",
                        "content": "\n".join(step["tool_results"]),
                        "thought": None,
                        "tool_calls": [],
                        "tool_results": step["tool_results"],
                        "id": None,
                        "parentId": None,
                        "session_id": session_key,
                    }
                )

        if not formatted_messages:
            formatted_messages.append(
                {
                    "round": 1,
                    "role": "assistant",
                    "content": content,
                    "thought": None,
                    "tool_calls": [],
                    "tool_results": [],
                    "id": None,
                    "parentId": None,
                    "session_id": session_key,
                }
            )

        return {
            "task_id": "",  # Will be populated by the base class verification loop if needed, or passed in
            "final_output": content,
            "total_rounds": len(capture_hook.trajectory),
            "is_completed": True,
            "messages": formatted_messages,
            "metadata": {
                "session_key": session_key,
            },
        }
