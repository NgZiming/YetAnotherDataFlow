import json
import random
import re
import requests
import shutil
import time
import asyncio

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Generator
from typing_extensions import TypedDict

from tqdm import tqdm

from dataflow.logger import get_logger
from dataflow.utils.generate_binary_files import generate_file

from .user import UserSimulatorABC, SimulationResult


class MessageDict(TypedDict, total=True):
    """单条消息的字典格式。"""

    round: int  # 所属轮次
    role: str  # "system" | "user" | "assistant" | "tool" | "toolResult"
    content: str  # 消息内容
    thought: Optional[str]  # 思考内容 (如有)
    tool_calls: List[Dict]  # 工具调用列表 (如有)
    tool_results: List[Dict]  # 工具结果列表 (如有)
    id: Optional[str]  # 消息 ID(用于拓扑)
    parentId: Optional[str]  # 父消息 ID(用于拓扑)
    session_id: Optional[str]  # 所属 session ID


class TrajectoryDict(TypedDict, total=True):
    """
    标准轨迹字典格式 (子类格式化结果时使用此类型)。
    参考 OpenClawTrajectorySimplifierOperator 的字段保留策略。
    """

    task_id: str  # 任务 ID
    final_output: str  # 最终输出文本
    total_rounds: int  # 总轮数
    is_completed: bool  # 是否通过验证完成
    messages: List[MessageDict]  # 消息列表 (按时间顺序)
    metadata: Dict[str, Any]  # 元数据 (子类可扩展)


class TaskDefinition(TypedDict):
    """
    任务定义的结构化契约，用于传递完整的任务信息到 Serving 层。

    包含：
    - 核心引导数据：question, milestones, dialogue_scripts
    - 资源定义：input_files_data, input_skills_data
    - 执行控制：max_rounds, global_context
    """

    task_id: str
    question: str
    milestones: List[Dict[str, Any]]
    dialogue_scripts: List[Dict[str, Any]]
    files_contents: Dict[str, Any]
    skills: List[str]
    max_rounds: int
    global_context: Dict[str, Any]


"""
AgentServingABC - Agent 能力流程抽象基类

提供复杂 Agent 能力的统一流程抽象，包括:
- 验证循环控制
- 文件准备与增量检测
- 外部 LLM 验证
- 并发调度

子类只需实现与具体运行时相关的 4 个抽象方法。
"""


class AgentServingABC(ABC):
    """
    Agent 能力流程抽象基类。

    提供复杂 Agent 任务的统一流程:
    1. 文件准备到 workspace
    2. 执行上下文管理 (session/agent 创建)
    3. 多轮验证循环
    4. 并发调度

    子类需实现 4 个抽象方法:
    - _get_workspace_path: 获取 workspace 路径
    - _prepare_execution_context: 准备执行上下文
    - _send_query: 发送查询并获取响应
    - _cleanup_execution_context: 清理执行上下文
    """

    def __init__(
        self,
        user: UserSimulatorABC,
        max_workers: int = 4,
        max_retries: int = 3,
    ):
        """
        初始化 AgentServingABC。

        Args:
            max_workers: 最大并发数
            max_retries: 最大重试次数
            timeout: 超时时间 (秒)
            user_simulator_api_url: 用户模拟器 LLM API URL（必需）
            user_simulator_api_key: 用户模拟器 LLM API Key（可选）
            user_simulator_client_params: 用户模拟器 LLM 的其他参数 (model, temperature 等)，有默认配置
            user_simulator_prompts: 用户模拟器的 Prompt 模板集 (必需)
        """
        self.logger = get_logger()
        self.user = user
        self.max_workers = max_workers
        self.max_retries = max_retries

    # =========================================================================
    # 抽象方法 - 子类必须实现
    # =========================================================================

    @abstractmethod
    def _get_workspace_path(self, task_id: str) -> Path:
        """
        获取任务的 workspace 路径。

        Args:
            task_id: 任务标识符

        Returns:
            workspace 目录路径
        """
        pass

    @abstractmethod
    def _prepare_execution_context(
        self,
        workspace_path: Path,
        input_files_data: Dict[str, Any],
        input_skills_data: List[str],
        task_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        准备执行上下文 (session/agent 创建、文件准备等)。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            task_id: 任务 ID(用于动态创建/选择 agent)

        Returns:
            路径映射字典 {原始路径：实际生成路径}(可选),用于后续文件增量检测时排除
        """
        pass

    @abstractmethod
    def _send_query(
        self,
        workspace_path: Path,
        query: str,
        current_time: Optional[str] = None,
    ) -> TrajectoryDict:
        """
        发送查询并获取响应 (子类内部负责格式化轨迹)。

        Args:
            workspace_path: workspace 路径
            query: 用户查询
            current_time: 当前时间字符串，如果为 None 则使用默认时间

        Returns:
            标准轨迹字典 (子类内部格式化):
            {
                "messages": List[MessageDict],
                "final_output": str,
                "files_created": List[str],
                "errors": List[str],
                "metadata": Dict,
            }
        """
        pass

    @abstractmethod
    def _cleanup_execution_context(
        self, workspace_path: Path, task_id: Optional[str] = None
    ) -> None:
        """
        清理执行上下文资源。

        Args:
            workspace_path: workspace 路径
            task_id: 任务 ID(用于匹配对应的 agent)
        """
        pass

    @contextmanager
    def _manage_execution_context(
        self,
        workspace_path: Path,
        input_files_data: Dict[str, Any],
        input_skills_data: List[str],
        task_id: Optional[str] = None,
    ) -> Generator[Dict[str, str], None, None]:
        """
        管理完整的执行上下文生命周期 (contextmanager 包装器)。

        确保：无论成功失败都会清理资源。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            task_id: 任务 ID(用于匹配对应的 agent)

        Yields:
            路径映射字典 {原始路径：实际生成路径}(可选)
        """
        path_mapping = self._prepare_execution_context(
            workspace_path, input_files_data, input_skills_data, task_id
        )
        try:
            yield path_mapping
        finally:
            self._cleanup_execution_context(workspace_path, task_id)

    # =========================================================================
    # 通用实现 - 子类可直接使用
    # =========================================================================

    def _prepare_files(
        self,
        workspace_path: Path,
        input_files_data: Dict[str, Any],
        input_skills_data: List[str],
        skill_base_dir: str,
    ) -> Dict[str, str]:
        """
        将文件和 skills 准备到 workspace 目录。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据 (key 为原始文件路径)
            input_skills_data: skill 名称列表
            skill_base_dir: Skill 基础目录（必需）

        Returns:
            文件路径映射字典 {原始路径：实际生成路径}
        """
        path_mapping = {}

        # 准备文件到 workspace (保留原目录结构)
        if input_files_data:
            for original_path, content_data in input_files_data.items():
                if not content_data or not isinstance(content_data, dict):
                    continue

                # 1. 将 /workspace/ 前缀去掉，获取相对路径
                rel_path_str = original_path
                if rel_path_str.startswith("/workspace/"):
                    rel_path_str = rel_path_str[len("/workspace/") :]

                # 2. 构建真实的绝对路径
                actual_path_obj = workspace_path / rel_path_str

                # 3. 确保父目录存在
                actual_path_obj.parent.mkdir(parents=True, exist_ok=True)

                # 4. 生成文件
                filename = actual_path_obj.name
                dir_path = str(actual_path_obj.parent)

                generate_file(
                    {"filename": filename, "content": content_data},
                    dir_path,
                )

                actual_path = str(actual_path_obj)
                path_mapping[original_path] = actual_path
                self.logger.debug(f"生成文件:{filename} -> {actual_path}")

        # 准备 skills 到 skills 目录
        if input_skills_data:
            skills_dir = workspace_path / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)

            for skill_name in input_skills_data:
                src_path = Path(skill_base_dir) / skill_name

                if not src_path.exists() or not src_path.is_dir():
                    raise Exception(f"Skill 路径不存在:{src_path}")

                dst_path = skills_dir / src_path.name
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
                self.logger.debug(f"拷贝 skill: {src_path} -> {dst_path}")

        return path_mapping

    def _replace_file_paths_in_text(
        self,
        text: str,
        path_mapping: Dict[str, str],
        workspace_path: Optional[Path] = None,
    ) -> str:
        """
        在文本中替换文件路径。

        Args:
            text: 原始文本
            path_mapping: 路径映射 {原始路径：实际路径}
            workspace_path: agent 的 workspace 路径 (用于处理 /workspace/ 到实际 workspace 的映射)

        Returns:
            替换后的文本
        """
        if not path_mapping and not workspace_path:
            return text

        result = text

        # 1. 先应用 path_mapping 中的精确替换 (按长度降序排序，避免部分匹配)
        if path_mapping:
            for original_path, actual_path in sorted(
                path_mapping.items(), key=lambda x: len(x[0]), reverse=True
            ):
                result = result.replace(original_path, actual_path)

        # 2. 处理 /workspace/ 前缀的通用映射
        # 只要出现 /workspace/xxx，都映射到真实的 workspace_path/xxx
        if workspace_path:
            # 匹配 /workspace/ 及其后面直到空白或引号的部分
            workspace_pattern = r'/workspace/([^\s"\'<>]+)'

            def replace_workspace(match):
                relative_path = match.group(1)
                # 构建绝对路径并规范化 (解决 ../ 等问题)
                actual_path = (workspace_path / relative_path).resolve()
                return str(actual_path)

            result = re.sub(workspace_pattern, replace_workspace, result)

            # 额外处理常见的 ~/ 缩写 (如果出现在路径上下文中)
            # 简单处理：将 ~/ 替换为 workspace_path (仅当它像个路径时)
            result = re.sub(
                r"(?<=[\s\"\'\)])~/([^\s\"\'<>]+)",
                lambda m: str((workspace_path / m.group(1)).resolve()),
                result,
            )

        return result

    def _get_new_file_contents(
        self,
        workspace_path: Path,
        path_mapping: Dict[str, str],
    ) -> dict[str, str]:
        """
        检查整个 workspace 目录中的文件，返回新增文件的内容（或总结）。

        Args:
            workspace_path: workspace 路径
            path_mapping: 路径映射 {原始路径：实际生成路径}
            max_total_len: 总内容长度限制（默认 8000 字符，避免 prompt 过长）
            summary_mode: 是否使用总结模式（True=只发送文件大纲 + 总结，False=发送完整内容）
            use_llm_summary: 是否使用 LLM 总结大文件（需要额外 LLM 调用，更智能但成本高）

        Returns:
            新增文件的内容字符串（或总结）
        """
        if not workspace_path.exists():
            return {}

        # 排除列表
        EXCLUDED_FILES = {
            "AGENTS.md",
            "BOOTSTRAP.md",
            "HEARTBEAT.md",
            "IDENTITY.md",
            "SOUL.md",
            "TOOLS.md",
            "USER.md",
        }
        EXCLUDED_DIRS = {"skills", "sessions", "agent", ".openclaw"}

        # 从 path_mapping 中提取实际生成的文件名集合
        initial_files_set = set(Path(p).name for p in path_mapping.values())
        file_contents = {}

        # 递归扫描整个 workspace
        files = []
        for f in workspace_path.rglob("*"):
            # 检查是否在排除目录中
            if any(part in EXCLUDED_DIRS for part in f.parts):
                continue

            # 只处理文件，且排除预定义文件和初始文件
            if (
                f.is_file()
                and f.name not in EXCLUDED_FILES
                and f.name not in initial_files_set
            ):
                files.append(f)

        # 按文件大小排序，优先处理小文件
        files.sort(key=lambda f: f.stat().st_size)

        for f in files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                rel_path = f.relative_to(workspace_path)
                file_contents[str(rel_path)] = content
            except Exception:
                self.logger.exception(f"读取文件 {f} 失败")

        return file_contents

    def generate_from_input(
        self,
        tasks: List[TaskDefinition],
    ) -> List[Optional[TrajectoryDict]]:
        """
        并发执行一组定义好的任务。

        Args:
            tasks: 任务定义列表，每个任务包含完整的引导数据和资源定义

        Returns:
            标准轨迹字典列表
        """
        if not tasks:
            return []

        results: list[Optional[TrajectoryDict]] = [None] * len(tasks)

        def execute_task(idx: int):
            try:
                task = tasks[idx]
                return self._execute_single_task_with_verification(
                    task_id=task["task_id"],
                    input_files_data=task["files_contents"],
                    input_skills_data=task["skills"],
                    question=task["question"],
                    milestones=task["milestones"],
                    dialogue_scripts=task["dialogue_scripts"],
                    max_rounds=task["max_rounds"],
                )
            except Exception as e:
                self.logger.exception(f"Task {idx} failed: {e}")
                return None

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(execute_task, i): i for i in range(len(tasks))}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()

        return results

    def _execute_single_task_with_verification(
        self,
        task_id: str,
        input_files_data: Dict[str, Any],
        input_skills_data: List[str],
        question: str,
        milestones: List[Dict[str, Any]],
        dialogue_scripts: List[Dict[str, Any]],
        max_rounds: int = 1,
    ) -> TrajectoryDict:
        """
        执行单个任务，支持验证循环。

        Args:
            task_id: 任务标识符
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            question: 任务问题
            milestones: 合并后的里程碑列表（包含 expected_retrieval_point）
            dialogue_scripts: 对话脚本列表
            max_rounds: 最大轮数 (>1 时启用验证循环)

        Returns:
            完整轨迹字典
        """
        self.logger.info(f"开始任务:{task_id}")

        # 随机等待，避免大量 agent 同时创建
        random_delay = random.uniform(1.0, 3.0)
        self.logger.debug(f"任务 {task_id} 随机等待 {random_delay:.2f} 秒后启动")
        time.sleep(random_delay)

        # 状态变量初始化
        workspace_path = self._get_workspace_path(task_id)

        for retry_attempt in range(self.max_retries):
            round_num = 0
            conversation = ""
            all_final_outputs: List[str] = []
            all_feedbacks: List[str] = []
            final_output = ""
            is_completed = False
            messages: List[MessageDict] = []

            try:
                with self._manage_execution_context(
                    workspace_path,
                    input_files_data,
                    input_skills_data,
                    task_id,
                ) as path_mapping:
                    path_mapping = path_mapping or {}

                    execution_start_time = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S CST"
                    )

                    while round_num < max_rounds:
                        round_num += 1
                        self.logger.info(f"[轮次 {round_num}/{max_rounds}] 执行任务...")

                        # 构造模拟器输入
                        raw_data = {
                            "question": question,
                            "milestones": milestones,
                            "dialogue_scripts": dialogue_scripts,
                            "feedbacks": all_feedbacks,
                            "agent_outputs": all_final_outputs,
                            "file_contents": self._get_new_file_contents(
                                workspace_path, path_mapping
                            ),
                            "global_context": {},
                        }

                        # 同步调用异步模拟器
                        try:
                            loop = asyncio.get_event_loop()
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)

                        sim_result: SimulationResult = loop.run_until_complete(
                            self.user.run(raw_data)
                        )
                        final_res = sim_result["final_response"]

                        judgment = final_res.get("judgment", "aborted")
                        is_completed = judgment == "completed"
                        conversation = final_res.get("feedback", "")

                        if (
                            is_completed
                            or judgment == "aborted"
                            or round_num == max_rounds
                        ):
                            return {
                                "task_id": task_id,
                                "final_output": final_output,
                                "total_rounds": round_num,
                                "is_completed": is_completed,
                                "messages": messages,
                                "metadata": {
                                    "max_retries": self.max_retries,
                                    "retry_attempt": retry_attempt,
                                },
                            }
                        if not conversation:
                            raise Exception("user feedback is empty")

                        all_feedbacks.append(conversation)
                        round_result = self._send_query(
                            workspace_path,
                            conversation,
                            current_time=execution_start_time,
                        )

                        messages = round_result.get("messages", [])
                        final_output = round_result.get("final_output", "")
                        all_final_outputs.append(final_output)

            except Exception:
                self.logger.exception(
                    f"Retry {retry_attempt + 1}/{self.max_retries} failed"
                )
                if retry_attempt < self.max_retries - 1:
                    time.sleep(5 * (retry_attempt + 1))
                continue

        return {
            "task_id": task_id,
            "final_output": "",
            "total_rounds": round_num,
            "is_completed": False,
            "messages": messages,
            "metadata": {
                "max_retries": self.max_retries,
                "retry_attempt": retry_attempt,
            },
        }
