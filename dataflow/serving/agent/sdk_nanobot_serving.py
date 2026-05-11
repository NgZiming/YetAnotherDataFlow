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
import shutil

from dataclasses import asdict
from pathlib import Path
from typing import List, Dict, Optional, Any

from dataflow.logger import get_logger

# 导入 Nanobot 内部组件用于构建高保真 System Prompt
from nanobot.agent.hook import AgentHook, AgentHookContext

# 导入 AgentServingABC 基类
from dataflow.core.agentic import AgentServingABC, TrajectoryDict, UserSimulatorABC
from dataflow.serving.agent.nanobot_2_std_format import convert as nanobot_convert


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
        """
        self.logger = get_logger()

        self.config_path = Path(config_path).expanduser()
        self.workspace_basedir = Path(workspace).expanduser()
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
        # 移除了 self.bot 全局单例，改为在任务上下文中按需创建
        self.bot_instances: Dict[Path, Any] = {}

        # 自动创建/更新配置
        if auto_create_config:
            self._create_config(force=True)

        self.logger.info(
            f"SDKNanobotServing 初始化：workspace_basedir={self.workspace_basedir}, model={self.model}"
        )

    def _create_config(self, force: bool = False) -> None:
        """创建配置文件"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_basedir.mkdir(parents=True, exist_ok=True)

        config_data: Dict[str, Any] = {
            "agents": {
                "defaults": {
                    "workspace": str(self.workspace_basedir),
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
                "restrictToWorkspace": True,
            },
            "gateway": {"heartbeat": {"enabled": False}},
        }

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Nanobot 配置已创建：{self.config_path}")

    # =========================================================================
    # Trajectory Capture Hook
    # =========================================================================

    class TrajectoryCaptureHook(AgentHook):
        """
        Custom hook to capture full agent trajectory in memory.
        Implements the AgentHook interface required by nanobot.
        """

        def __init__(self) -> None:
            super().__init__()
            self.context = None
            self.logger = get_logger()

        async def after_iteration(self, context: Any) -> None:
            self.context = context

    def _get_workspace_path(self, task_id: str) -> Path:
        """获取任务的 workspace 路径（临时目录）。"""
        # 直接在 workspace_basedir 下创建任务目录，避开 .temp 排除名单
        workspace_path = self.workspace_basedir / f"task_{task_id}"
        workspace_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(
            f"Determined workspace path for task {task_id}: {workspace_path.resolve()}"
        )
        return workspace_path

    def _prepare_execution_context(
        self,
        workspace_path: Path,
        input_files_data: Dict,
        input_skills_data: List[str],
        task_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """准备执行上下文（创建临时目录、生成文件、注入性格）。"""
        self.logger.info(
            f"Preparing execution context for workspace: {workspace_path.resolve()}"
        )
        workspace_path.mkdir(parents=True, exist_ok=True)

        # 1. 为该隔离空间创建专属的 Nanobot 实例
        # 必须在写入文件之前创建 Bot 实例，防止 Bot 初始化过程清空 workspace 目录
        try:
            from nanobot import Nanobot

            self.logger.info(
                f"Creating Nanobot instance for workspace: {workspace_path.resolve()}"
            )
            bot = Nanobot.from_config(
                config_path=str(self.config_path),
                workspace=str(workspace_path.resolve()),
            )
            # 将实例存储在缓存中，以便 _send_query 可以访问
            self.bot_instances[workspace_path] = bot
            self.logger.info(
                f"Successfully created Bot instance for: {workspace_path.resolve()}"
            )
        except Exception as e:
            self.logger.error(f"创建任务专属 Bot 实例失败: {e}")
            raise e

        # 2. 性格注入: Nanobot ContextBuilder 自动加载根目录的 USER.md 和 SOUL.md
        if self.user_md:
            target = workspace_path / "USER.md"
            target.write_text(self.user_md, encoding="utf-8")
            self.logger.info(f"Injected USER.md into {target.resolve()}")
        if self.soul_md:
            target = workspace_path / "SOUL.md"
            target.write_text(self.soul_md, encoding="utf-8")
            self.logger.info(f"Injected SOUL.md into {target.resolve()}")

        # 3. 使用基类方法准备文件和技能，确保物理拷贝以实现完全隔离
        skill_base_dir = ""
        if self.extra_skills_dirs:
            for extra_dir in self.extra_skills_dirs:
                if extra_dir.exists() and extra_dir.is_dir():
                    skill_base_dir = str(extra_dir)
                    break

        self.logger.info(
            f"Preparing files and skills. Base skill dir: {skill_base_dir}"
        )
        path_mapping = self._prepare_files(
            workspace_path=workspace_path,
            input_files_data=input_files_data,
            input_skills_data=input_skills_data,
            skill_base_dir=skill_base_dir,
        )
        self.logger.info(f"Files prepared. Path mapping: {path_mapping}")

        return path_mapping

    def _cleanup_execution_context(
        self, workspace_path: Path, task_id: Optional[str] = None
    ) -> None:
        """清理执行上下文资源（删除临时目录）。"""
        self.logger.info(
            f"Cleaning up execution context for workspace: {workspace_path.resolve()}"
        )
        # 1. 从缓存中移除 Bot 实例，以便垃圾回收
        if workspace_path in self.bot_instances:
            del self.bot_instances[workspace_path]
            self.logger.info(f"Destroyed Bot instance for: {workspace_path.resolve()}")

        # 2. 删除临时目录
        if workspace_path.exists():
            try:
                shutil.rmtree(workspace_path)
                self.logger.info(
                    f"Successfully deleted temporary directory: {workspace_path.resolve()}"
                )
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
        # 因为现在每个任务拥有独立的 Bot 实例和隔离的 workspace，
        # 内部的会话状态已由 bot 实例和物理路径隔离，session_key 固定为 "main" 即可。
        session_key = "main"

        # 获取该任务空间专属的 Bot 实例
        bot = self.bot_instances.get(workspace_path)
        if not bot:
            self.logger.error(f"未找到任务专属 Bot 实例，空间路径: {workspace_path}")
            raise RuntimeError(
                f"Bot instance not found for workspace: {workspace_path}"
            )

        # 初始化轨迹捕获 Hook
        capture_hook = self.TrajectoryCaptureHook()

        # 执行异步查询
        async def run_query():
            # 注入自定义 Hook 以捕获详细轨迹，并设置超时时间（600s）
            return await asyncio.wait_for(
                bot.run(
                    query,
                    session_key=session_key,
                    hooks=[capture_hook],
                ),
                timeout=600,
            )

        try:
            _ = asyncio.run(run_query())
        except Exception as e:
            self.logger.error(f"Nanobot 运行失败: {e}")
            raise e

        context: AgentHookContext = capture_hook.context
        # DEBUG: 打印原始 capture_hook.trajectory 结构，以便分析
        self.logger.info(
            f"[Trajectory Debug] Raw trajectory data: {json.dumps(asdict(context), ensure_ascii=False, indent=2)}"
        )

        trajectory = nanobot_convert(context)

        return trajectory
