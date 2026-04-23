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
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Optional, Any, Iterator

from tqdm import tqdm

from dataflow.logger import get_logger

# 导入 AgentServingABC 基类
from dataflow.core.llm_serving import AgentServingABC, TrajectoryDict
from dataflow.utils.generate_binary_files import generate_file


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
        config_path: str = "~/.nanobot/config.json",
        workspace: str = "~/.nanobot/workspace",
        model: Optional[str] = None,
        provider: str = "vllm",
        api_base: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        max_workers: int = 4,
        timeout: int = 300,
        max_retries: int = 3,
        auto_create_config: bool = True,
        extra_skills_dirs: Optional[List[str]] = None,
        verification_api_url: Optional[str] = None,
        verification_api_key: Optional[str] = None,
        verification_client_params: Optional[Dict[str, Any]] = None,
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

        super().__init__(
            max_workers=max_workers,
            max_retries=max_retries,
            timeout=timeout,
            verification_api_url=verification_api_url,
            verification_api_key=verification_api_key,
            verification_client_params=verification_client_params,
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

    # =========================================================================
    # AgentServingABC 抽象方法实现
    # =========================================================================

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
    ) -> Optional[List[str]]:
        """准备执行上下文（创建临时目录、生成文件）。"""
        workspace_path.mkdir(parents=True, exist_ok=True)

        assets_dir = workspace_path / "assets"
        skills_dir = workspace_path / "skills"

        new_file_paths: List[str] = []

        # 生成文件到 assets 目录
        if input_files_data:
            assets_dir.mkdir(parents=True, exist_ok=True)
            for filename, content_data in input_files_data.items():
                if not content_data or not isinstance(content_data, dict):
                    continue
                new_path = assets_dir / Path(filename).name
                generate_file(
                    {"filename": new_path.name, "content": content_data},
                    str(assets_dir),
                )
                new_file_paths.append(str(new_path))

        # 链接技能目录（从 extra_skills_dirs）
        if input_skills_data and self.extra_skills_dirs:
            skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_name in input_skills_data:
                for extra_dir in self.extra_skills_dirs:
                    src_path = extra_dir / skill_name
                    if src_path.exists() and src_path.is_dir():
                        dst_path = skills_dir / skill_name
                        if not dst_path.exists():
                            dst_path.symlink_to(src_path, target_is_directory=True)
                        break

        return [Path(p).name for p in new_file_paths]

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
        发送查询并获取响应（从 session 文件提取完整轨迹）。

        Args:
            workspace_path: workspace 路径
            query: 用户查询
            current_time: 当前时间字符串，如果为 None 则使用默认时间

        Returns:
            标准轨迹字典 (AgentServingABC.TrajectoryDict)
        """
        session_key = f"serving-{id(self)}-{uuid.uuid4().hex[:8]}"

        # 执行异步查询
        async def run_query():
            return await self.bot.run(query, session_key=session_key)

        result = asyncio.run(run_query())
        content = result.content if result.content else ""

        # 从 session 文件提取轨迹
        # Nanobot 的 session 文件路径: workspace/sessions/{session_key}.jsonl
        session_file = self.workspace / "sessions" / f"{session_key}.jsonl"

        formatted_messages = []
        if session_file.exists():
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            m = json.loads(line)
                            # 映射 Nanobot 格式到 TrajectoryDict 格式
                            # 假设 m 包含 role, content, tool_calls 等
                            formatted_messages.append(
                                {
                                    "round": 0,  # 可以在这里根据消息索引计算 round
                                    "role": m.get("role", "unknown"),
                                    "content": m.get("content", ""),
                                    "thought": m.get("thought") or m.get("reasoning"),
                                    "tool_calls": m.get("tool_calls", []),
                                    "tool_results": m.get("tool_results", []),
                                    "id": m.get("id"),
                                    "parentId": m.get("parentId"),
                                    "session_id": session_key,
                                }
                            )
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                self.logger.error(f"读取 session 文件失败 {session_file}: {e}")

        if not formatted_messages:
            # fallback: 仅包含最终响应
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

        # 返回标准轨迹字典
        return {
            "messages": formatted_messages,
            "final_output": content,
            "files_created": [],
            "errors": [],
        }

    # =========================================================================
    # 公共接口
    # =========================================================================

    def generate_from_input(
        self,
        user_inputs: List[str],
        input_files_data: List[Dict],
        input_skills_data: List[List[str]],
        enable_verification: bool = False,
        verification_prompt_template: Optional[str] = None,
        max_verification_rounds: int = 3,
    ) -> List[TrajectoryDict]:
        """
        生成轨迹字典列表（并发执行）。

        Args:
            user_inputs: 用户输入列表
            input_files_data: 文件内容数据列表
            input_skills_data: skill 路径列表
            enable_verification: 是否启用验证
            verification_prompt_template: 验证提示词模板
            max_verification_rounds: 最大验证轮数

        Returns:
            List[Dict] - 轨迹字典列表
        """
        if not self._initialized:
            self.start_serving()

        if not user_inputs:
            self.logger.warning("user_inputs 为空，直接返回")
            return []

        if len(input_files_data) != len(user_inputs):
            raise ValueError(
                f"input_files_data 长度 ({len(input_files_data)}) "
                f"必须与 user_inputs 长度 ({len(user_inputs)}) 相同"
            )

        if len(input_skills_data) != len(user_inputs):
            raise ValueError(
                f"input_skills_data 长度 ({len(input_skills_data)}) "
                f"必须与 user_inputs 长度 ({len(user_inputs)}) 相同"
            )

        self.logger.info(f"开始处理 {len(user_inputs)} 个请求")

        async def run_all():
            semaphore = asyncio.Semaphore(self.max_workers)

            async def limited_task(i: int):
                async with semaphore:
                    # 使用父类 AgentServingABC 提供的 _execute_single_task_with_verification
                    return self._execute_single_task_with_verification(
                        f"task-{i}",
                        user_inputs[i],
                        input_files_data[i],
                        input_skills_data[i],
                        verification_prompt_template,
                        max_verification_rounds if enable_verification else 1,
                    )

            tasks = [limited_task(i) for i in range(len(user_inputs))]

            results: List[Dict[str, Any]] = [{} for _ in range(len(user_inputs))]
            completed = 0
            failed = 0

            with tqdm(total=len(user_inputs), desc="处理请求", unit="task") as pbar:
                for i, future in enumerate(asyncio.as_completed(tasks)):
                    try:
                        res = await future
                        results[i] = res
                        completed += 1
                    except Exception as e:
                        self.logger.error(f"[任务 {i + 1}] 失败：{e}")
                        results[i] = {
                            "task_id": f"task-{i}",
                            "task_description": user_inputs[i],
                            "final_output": "",
                            "total_rounds": 0,
                            "is_completed": False,
                            "messages": [],
                            "files_created": [],
                            "errors": [str(e)],
                            "metadata": {},
                        }
                        failed += 1
                    pbar.update(1)

            return results

        return asyncio.run(run_all())  # type: ignore

    def start_serving(self) -> None:
        """启动服务（确保 nanobot 已加载）。"""
        if self._initialized:
            return

        if not self.bot:
            self._load_nanobot()

        self._initialized = True
        self.logger.info("SDKNanobotServing 已启动")

    def cleanup(self) -> None:
        """清理资源。"""
        self._initialized = False

        temp_base = self.workspace / ".temp"
        if temp_base.exists():
            try:
                shutil.rmtree(temp_base)
                self.logger.info("临时目录已清理")
            except Exception as e:
                self.logger.error(f"清理临时目录失败：{e}")

        self.logger.info("SDKNanobotServing 已清理")


def create_nanobot_serving(
    config_path: str = "~/.nanobot/config.json",
    workspace: str = "~/.nanobot/workspace",
    model: Optional[str] = None,
    provider: str = "vllm",
    max_workers: int = 4,
    max_retries: int = 3,
    **kwargs,
) -> SDKNanobotServing:
    """
    创建 SDKNanobotServing 实例的工厂函数。
    """
    return SDKNanobotServing(
        config_path=config_path,
        workspace=workspace,
        model=model,
        provider=provider,
        max_workers=max_workers,
        max_retries=max_retries,
        **kwargs,
    )
