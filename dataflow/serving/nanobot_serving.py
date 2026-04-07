"""
NanobotServing - 基于 nanobot Python SDK 的轻量级 Serving 类

替代 CLIOpenClawServing，纯 Python 实现，无 CLI 依赖。

特点:
- 纯 Python 调用，无 subprocess 开销
- 自动创建配置文件，无需 CLI
- session_key 实现会话隔离
- 支持 async 原生并发
- 每个请求独立临时目录，避免并发冲突
- 支持外部技能目录（通过符号链接）

使用示例:
    from dataflow.serving import NanobotServing

    serving = NanobotServing(
        model="vllm//data/share/models/Qwen3.5-122B-A10B/",
        max_workers=4,
    )
    responses = serving.generate_from_input(["问题 1", "问题 2"])
"""

from __future__ import annotations

import asyncio
import json
import uuid
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Any

from tqdm import tqdm

from dataflow.core import LLMServingABC
from dataflow.logger import get_logger

# 导入二进制文件生成模块（复用现有逻辑）
from dataflow.utils.generate_binary_files import generate_file


class NanobotServing(LLMServingABC):
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
        max_workers: int = 4,
        timeout: int = 300,
        max_retries: int = 3,
        auto_create_config: bool = True,
        extra_skills_dirs: Optional[List[str]] = None,
    ):
        """
        初始化 NanobotServing。

        Args:
            config_path: 配置文件路径
            workspace: 工作目录
            model: 模型名称，如果为 None 则使用默认值
            provider: LLM Provider (vllm/openai/anthropic等)
            max_workers: 并发数
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
            auto_create_config: 是否自动创建配置
            extra_skills_dirs: 额外技能目录列表（会通过符号链接到 workspace）
        """
        self.logger = get_logger()

        self.config_path = Path(config_path).expanduser()
        self.workspace = Path(workspace).expanduser()
        self.model = model or "vllm//data/share/models/Qwen3.5-122B-A10B/"
        self.provider = provider
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra_skills_dirs = [
            Path(d).expanduser() for d in (extra_skills_dirs or [])
        ]
        self._initialized = False

        # 自动创建配置
        if auto_create_config and not self.config_path.exists():
            self._create_config()

        # 链接外部技能目录
        self._link_extra_skills()

        # 清理旧的临时目录（可选）
        self._cleanup_old_temp_dirs()

        # 加载 nanobot
        self._load_nanobot()

    def _cleanup_old_temp_dirs(self, max_age_hours: int = 1) -> None:
        """清理超过指定时间的临时目录（防止磁盘占满）"""
        temp_base = self.workspace / ".temp"
        if not temp_base.exists():
            return

        import time

        current_time = time.time()
        max_age_seconds = max_age_hours * 3600

        try:
            for temp_dir in temp_base.iterdir():
                if temp_dir.is_dir():
                    # 检查目录修改时间
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

        # 预先扫描已存在的技能（避免重复检查）
        existing_skills = {p.name for p in workspace_skills.iterdir() if p.exists()}

        linked_count = 0
        skipped_count = 0

        for extra_dir in self.extra_skills_dirs:
            if not extra_dir.exists():
                self.logger.warning(f"额外技能目录不存在，跳过：{extra_dir}")
                continue

            # 批量处理：先收集所有技能，再批量创建链接
            skills_to_link = []
            for skill_subdir in extra_dir.iterdir():
                if not skill_subdir.is_dir():
                    continue

                skill_name = skill_subdir.name
                if skill_name in existing_skills:
                    skipped_count += 1
                    continue

                skills_to_link.append((skill_name, skill_subdir))
                existing_skills.add(skill_name)  # 避免重复链接

            # 批量创建符号链接
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

    def _create_config(self) -> None:
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
                    "contextWindowTokens": 131072,  # 128K
                    "temperature": 0.1,
                    "dream": {
                        "intervalH": 2,
                    },
                }
            },
            "providers": {
                self.provider: {
                    "apiKey": "",
                    "apiBase": "",
                }
            },
            "tools": {
                "web": {
                    "enable": True,
                    "search": {
                        "provider": "duckduckgo",
                    },
                },
                "exec": {
                    "enable": True,
                    "timeout": 60,
                },
                "restrictToWorkspace": False,
            },
            "gateway": {
                "heartbeat": {
                    "enabled": False,
                }
            },
        }

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Nanobot 配置已创建：{self.config_path}")
        self.logger.info(f"  Workspace: {self.workspace}")
        self.logger.info(f"  Model: {self.model}")

    def _load_nanobot(self) -> None:
        """加载 nanobot 实例"""
        try:
            from nanobot import Nanobot

            self.bot = Nanobot.from_config(
                config_path=str(self.config_path),
                workspace=str(self.workspace),
            )
            self.logger.info("Nanobot 实例已加载")
        except Exception as e:
            self.logger.error(f"加载 Nanobot 失败：{e}")
            raise

    async def _execute_single(
        self,
        session_key: str,
        user_query: str,
        input_files_data: Optional[Dict],
    ) -> str:
        """
        执行单个查询（带重试）。

        Args:
            session_key: 会话标识符（用于隔离）
            user_query: 用户查询
            input_files_data: 文件内容数据

        Returns:
            回复文本
        """
        # 为每个请求创建独立的临时目录（避免并发冲突）
        temp_dir: Optional[Path] = None
        try:
            if input_files_data:
                # 创建唯一临时目录：workspace/.temp/{session_key}_{uuid}
                temp_base = self.workspace / ".temp"
                temp_base.mkdir(parents=True, exist_ok=True)
                temp_dir = temp_base / f"{session_key}_{uuid.uuid4().hex[:8]}"
                temp_dir.mkdir(parents=True, exist_ok=True)

                # 生成文件到临时目录
                new_file_paths: List[str] = []
                for filename, content_data in input_files_data.items():
                    if not content_data or not isinstance(content_data, dict):
                        continue
                    new_path = temp_dir / Path(filename).name
                    generate_file(
                        {"filename": new_path.name, "content": content_data},
                        str(temp_dir),
                    )
                    new_file_paths.append(str(new_path))

                # 添加文件引用到查询
                query = user_query
                if len(new_file_paths) > 0:
                    query += f"\n下面是任务相关的文件：\n{new_file_paths}"
            else:
                query = user_query

            # 执行查询（带重试）
            last_error: Optional[Exception] = None
            for attempt in range(self.max_retries + 1):
                try:
                    result = await asyncio.wait_for(
                        self.bot.run(query, session_key=session_key),
                        timeout=self.timeout,
                    )
                    return result.content if result.content else ""
                except asyncio.TimeoutError:
                    last_error = asyncio.TimeoutError(f"请求超时 ({self.timeout}s)")
                    if attempt < self.max_retries:
                        backoff = 2**attempt
                        self.logger.warning(
                            f"请求超时，{backoff}s 后重试... (attempt {attempt + 1}/{self.max_retries + 1})"
                        )
                        await asyncio.sleep(backoff)
                    else:
                        self.logger.error(f"请求失败：{last_error}")
                        raise
                except Exception as e:
                    last_error = e
                    if attempt < self.max_retries:
                        backoff = 2**attempt
                        self.logger.warning(
                            f"请求失败，{backoff}s 后重试... (attempt {attempt + 1}/{self.max_retries + 1}): {e}"
                        )
                        await asyncio.sleep(backoff)
                    else:
                        self.logger.error(f"请求失败：{last_error}")
                        raise

            return ""

        finally:
            # 清理临时目录（无论成功失败都清理）
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    self.logger.error(f"清理临时目录失败 {temp_dir}: {e}")

    async def generate_async(
        self,
        user_inputs: List[str],
        input_files_data: List[Dict],
    ) -> List[str]:
        """
        异步并发执行（推荐）。

        Args:
            user_inputs: 用户输入列表
            input_files_data: 与 user_inputs 长度相同，每个元素是对应 query 的文件内容数据

        Returns:
            回复列表，长度与输入相同
        """
        if not user_inputs:
            return []

        if len(input_files_data) != len(user_inputs):
            raise ValueError(
                f"input_files_data 长度 ({len(input_files_data)}) "
                f"必须与 user_inputs 长度 ({len(user_inputs)}) 相同"
            )

        total = len(user_inputs)
        results: List[str] = [""] * total

        # 限制并发数
        semaphore = asyncio.Semaphore(self.max_workers)

        async def limited_task(i: int, query: str, files: Dict) -> str:
            async with semaphore:
                # 每个请求有唯一的 session_key
                session_key = f"serving-{id(self)}-{i}-{uuid.uuid4().hex[:8]}"
                return await self._execute_single(session_key, query, files)

        # 并发执行
        tasks = [
            limited_task(i, query, files)
            for i, (query, files) in enumerate(zip(user_inputs, input_files_data))
        ]

        # 使用 tqdm 显示进度
        completed = 0
        failed = 0

        with tqdm(total=total, desc="处理请求", unit="task") as pbar:
            for i, future in enumerate(asyncio.as_completed(tasks)):
                try:
                    results[i] = await future
                    completed += 1
                except Exception as e:
                    self.logger.error(f"[任务 {i + 1}/{total}] 失败：{e}")
                    results[i] = ""
                    failed += 1

                pbar.update(1)
                pbar.set_postfix({"成功": completed, "失败": failed})

        return results

    def generate_from_input(
        self,
        user_inputs: List[str],
        system_prompt: str,
        json_schema: dict,
        input_files_data: List[Dict],
    ) -> List[str]:
        """
        生成文本响应（兼容 CLIOpenClawServing 接口）。

        Args:
            user_inputs: 用户输入列表
            system_prompt: 系统提示词（CLI 模式下不使用）
            json_schema: JSON schema（CLI 模式下不使用）
            input_files_data: 与 user_inputs 长度相同，每个元素是对应 query 的文件内容数据

        Returns:
            回复列表，长度与输入相同，失败位置为空字符串
        """
        if not self._initialized:
            self.start_serving()

        return asyncio.run(self.generate_async(user_inputs, input_files_data))

    def generate_embedding_from_input(self, texts: List[str]) -> List[List[float]]:
        """生成嵌入向量（nanobot 不支持，返回空向量）"""
        self.logger.warning("NanobotServing 不支持 embedding，返回空向量")
        return [[] for _ in texts]

    def start_serving(self) -> None:
        """启动服务（确保 nanobot 已加载）"""
        if self._initialized:
            return

        if not self.bot:
            self._load_nanobot()

        self._initialized = True
        self.logger.info("NanobotServing 已启动")

    def cleanup(self) -> None:
        """清理资源"""
        self._initialized = False

        # 清理临时目录
        temp_base = self.workspace / ".temp"
        if temp_base.exists():
            try:
                shutil.rmtree(temp_base)
                self.logger.info("临时目录已清理")
            except Exception as e:
                self.logger.error(f"清理临时目录失败：{e}")

        self.logger.info("NanobotServing 已清理")


def create_nanobot_serving(
    config_path: str = "~/.nanobot/config.json",
    workspace: str = "~/.nanobot/workspace",
    model: Optional[str] = None,
    provider: str = "vllm",
    max_workers: int = 4,
    max_retries: int = 3,
    **kwargs,
) -> NanobotServing:
    """
    创建 NanobotServing 实例的工厂函数。

    Args:
        config_path: 配置文件路径
        workspace: 工作目录
        model: 模型名称
        provider: LLM Provider
        max_workers: 并发数
        max_retries: 最大重试次数
        **kwargs: 其他参数（timeout, auto_create_config, extra_skills_dirs）

    Returns:
        NanobotServing 实例
    """
    return NanobotServing(
        config_path=config_path,
        workspace=workspace,
        model=model,
        provider=provider,
        max_workers=max_workers,
        max_retries=max_retries,
        **kwargs,
    )
