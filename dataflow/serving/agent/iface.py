"""
AgentServingABC - Agent 能力流程抽象基类

提供复杂 Agent 能力的统一流程抽象，包括：
- 验证循环控制
- 文件准备与增量检测
- 外部 LLM 验证
- 并发调度

子类只需实现与具体运行时相关的 4 个抽象方法。
"""

from __future__ import annotations

import requests
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Iterator
from typing_extensions import TypedDict

from tqdm import tqdm

from dataflow.logger import get_logger
from dataflow.utils.generate_binary_files import generate_file


# =========================================================================
# 轨迹字典类型定义（在类定义后，避免循环导入）
# =========================================================================


class MessageDict(TypedDict, total=False):
    """单条消息的字典格式。"""

    round: int  # 所属轮次
    role: str  # "system" | "user" | "assistant" | "tool" | "toolResult"
    content: str  # 消息内容
    thought: Optional[str]  # 思考内容（如有）
    tool_calls: List[Dict]  # 工具调用列表（如有）
    tool_results: List[Dict]  # 工具结果列表（如有）
    id: Optional[str]  # 消息 ID（用于拓扑）
    parentId: Optional[str]  # 父消息 ID（用于拓扑）


class TrajectoryDict(TypedDict, total=False):
    """
    标准轨迹字典格式（子类格式化结果时使用此类型）。
    参考 OpenClawTrajectorySimplifierOperator 的字段保留策略。
    """

    task_id: str  # 任务 ID
    task_description: str  # 任务描述
    final_output: str  # 最终输出文本
    total_rounds: int  # 总轮数
    is_completed: bool  # 是否通过验证完成
    messages: List[MessageDict]  # 消息列表（按时间顺序）
    files_created: List[str]  # 创建的文件路径列表
    errors: List[str]  # 错误信息列表（如有）
    metadata: Dict[str, Any]  # 元数据（子类可扩展）


class AgentServingABC(ABC):
    """
    Agent 能力流程抽象基类。

    提供复杂 Agent 任务的统一流程：
    1. 文件准备到 workspace
    2. 执行上下文管理（session/agent 创建）
    3. 多轮验证循环
    4. 并发调度

    子类需实现 4 个抽象方法：
    - _get_workspace_path: 获取 workspace 路径
    - _prepare_execution_context: 准备执行上下文
    - _send_query: 发送查询并获取响应
    - _cleanup_execution_context: 清理执行上下文
    """

    def __init__(
        self,
        max_workers: int = 4,
        max_retries: int = 3,
        timeout: int = 300,
        verification_api_url: Optional[str] = None,
        verification_api_key: Optional[str] = None,
        verification_client_params: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化 AgentServingABC。

        Args:
            max_workers: 最大并发数
            max_retries: 最大重试次数
            timeout: 超时时间（秒）
            verification_api_url: 验证用 LLM API URL
            verification_api_key: 验证用 LLM API Key
            verification_client_params: 验证 LLM 的其他参数（model, temperature 等）
        """
        self.logger = get_logger()
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.timeout = timeout

        # 验证配置
        self._verification_api_url = verification_api_url
        self._verification_api_key = verification_api_key
        self._verification_client_params = {
            "model_name": "/data/share/models/Qwen3.5-122B-A10B/",
            "max_workers": 10,
            "max_completion_tokens": 16384,
            "read_timeout": 600,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        }

        if verification_client_params:
            self._verification_client_params.update(verification_client_params)

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
    ) -> Optional[List[str]]:
        """
        准备执行上下文（session/agent 创建、文件准备等）。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            task_id: 任务 ID（用于动态创建/选择 agent）

        Returns:
            初始文件名称列表（可选），用于后续文件增量检测时排除
        """
        pass

    @abstractmethod
    def _send_query(
        self,
        workspace_path: Path,
        query: str,
        add_assets_info: bool = False,
        current_time: Optional[str] = None,
    ) -> TrajectoryDict:
        """
        发送查询并获取响应（子类内部负责格式化轨迹）。

        Args:
            workspace_path: workspace 路径
            query: 用户查询
            add_assets_info: 是否添加文件信息到查询
            current_time: 当前时间字符串，如果为 None 则使用默认时间

        Returns:
            标准轨迹字典（子类内部格式化）:
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
            task_id: 任务 ID（用于匹配对应的 agent）
        """
        pass

    @contextmanager
    def _manage_execution_context(
        self,
        workspace_path: Path,
        input_files_data: Dict[str, Any],
        input_skills_data: List[str],
        task_id: Optional[str] = None,
    ) -> Iterator[Optional[List[str]]]:
        """
        管理完整的执行上下文生命周期（contextmanager 包装器）。

        确保：无论成功失败都会清理资源。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            task_id: 任务 ID（用于匹配对应的 agent）

        Yields:
            初始文件名称列表（可选），用于后续文件增量检测时排除
        """
        initial_files = self._prepare_execution_context(
            workspace_path, input_files_data, input_skills_data, task_id
        )
        try:
            yield initial_files
        finally:
            self._cleanup_execution_context(workspace_path, task_id)

    # =========================================================================
    # 通用实现 - 子类可直接使用
    # =========================================================================

    def _prepare_files(
        self,
        workspace_path: Path,
        input_files_data: Dict[str, Any],
    ) -> List[str]:
        """
        将文件准备到 workspace/assets 目录。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据

        Returns:
            生成的文件路径列表
        """
        if not input_files_data:
            return []

        assets_dir = workspace_path / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        generated_paths = []
        for filename, content_data in input_files_data.items():
            if not content_data or not isinstance(content_data, dict):
                continue

            new_path = assets_dir / Path(filename).name
            generate_file(
                {"filename": new_path.name, "content": content_data},
                str(assets_dir),
            )
            generated_paths.append(str(new_path))
            self.logger.debug(f"生成文件：{new_path.name}")

        return generated_paths

    def _get_new_file_contents(
        self,
        workspace_path: Path,
        initial_file_names: List[str],
    ) -> str:
        """
        检查 workspace/assets 目录中的文件，返回新增文件的内容。

        Args:
            workspace_path: workspace 路径
            initial_file_names: 初始文件列表（排除这些文件）

        Returns:
            新增文件的内容字符串
        """
        assets_dir = workspace_path / "assets"
        if not assets_dir.exists():
            return ""

        initial_files_set = set(initial_file_names)
        file_contents = []

        for f in assets_dir.iterdir():
            if f.is_file() and f.name not in initial_files_set:
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    if len(content) > 5000:
                        content = content[:5000] + "\n...（内容被截断）"
                    file_contents.append(f"【{f.name}】\n{content}\n")
                except Exception as e:
                    self.logger.warning(f"读取文件 {f} 失败：{e}")

        return "\n".join(file_contents) if file_contents else ""

    def _verify_with_external_llm(
        self,
        task_description: str,
        feedbacks: List[str],
        agent_outputs: List[str],
        prompt_template: Optional[str],
        workspace_path: Path,
        initial_file_names: List[str],
    ) -> Tuple[bool, str]:
        """
        使用外部 LLM 进行验证。

        Args:
            task_description: 任务描述
            feedbacks: 之前每一轮的反馈意见
            agent_outputs: Agent 每一轮的输出
            prompt_template: 验证提示词模板
            workspace_path: workspace 路径
            initial_file_names: 初始文件列表

        Returns:
            (是否完成，反馈信息)
        """
        if not self._verification_api_url:
            self.logger.warning("验证用 API URL 未设置，跳过验证")
            return True, ""

        # 获取新增文件内容
        file_contents_str = self._get_new_file_contents(
            workspace_path, initial_file_names
        )

        # 构建验证提示词
        if not prompt_template:
            verification_prompt = (
                f"请评估以下任务是否完成:\n\n"
                f"任务:{task_description}\n\n"
                f"之前每一轮的反馈意见:\n{feedbacks}\n\n"
                f"Agent 每一轮的输出:\n{agent_outputs}\n\n"
                f"Agent 创建的文件内容:\n{file_contents_str}\n\n"
                f"请返回:\n判断:completed/incomplete\n反馈:(如果未完成，给出具体的改进建议)"
            )
        else:
            verification_prompt = prompt_template.format(
                task_description=task_description,
                agent_outputs=agent_outputs,
                feedbacks=feedbacks,
                file_contents=file_contents_str,
            )

        try:
            self.logger.info(
                f"向验证 LLM 发起请求 (prompt 长度={len(verification_prompt)}字符)"
            )

            payload = {
                "model": self._verification_client_params.get("model", "default"),
                "messages": [{"role": "user", "content": verification_prompt}],
            }
            payload.update(self._verification_client_params)

            headers = {"Content-Type": "application/json"}
            if self._verification_api_key:
                headers["Authorization"] = f"Bearer {self._verification_api_key}"

            response = requests.post(
                self._verification_api_url,
                headers=headers,
                json=payload,
                timeout=self._verification_client_params.get("timeout", 600),
            )

            if response.status_code != 200:
                self.logger.error(
                    f"验证请求失败：status={response.status_code}, body={response.text[:500]}"
                )
                return True, ""

            result = response.json()
            verify_output = (
                result.get("choices", [{}])[0].get("message", {}).get("content", "")
            )

            self.logger.info(
                f"验证完成，输出：{verify_output[:100]}..."
                if verify_output
                else "验证完成，无输出"
            )
            return self._parse_verification_result(verify_output)

        except requests.exceptions.ConnectTimeout:
            self.logger.exception("验证 LLM 连接超时")
            raise
        except requests.exceptions.ReadTimeout:
            self.logger.exception("验证 LLM 读取超时")
            raise
        except Exception:
            self.logger.exception("验证 LLM 调用失败")
            raise

    def _parse_verification_result(self, verify_output: str) -> Tuple[bool, str]:
        """
        解析验证结果。

        Args:
            verify_output: LLM 验证输出

        Returns:
            (是否完成，反馈信息)

        Raises:
            Exception: 当反馈为空或格式不正确时
        """
        verify_output = verify_output.lower()

        is_completed = (
            "completed" in verify_output and "incomplete" not in verify_output
        )

        feedback = ""
        if "反馈:" in verify_output:
            feedback = verify_output.split("反馈:")[-1].strip()
        elif "反馈：" in verify_output:
            feedback = verify_output.split("反馈：")[-1].strip()
        else:
            self.logger.warning(f"验证输出中未找到'反馈:'字段")
            raise Exception("反馈找不到")

        if not feedback:
            raise Exception("验证反馈为空，LLM 未提供有效的改进建议")

        return is_completed, feedback

    def _execute_single_task_with_verification(
        self,
        task_id: str,
        task_description: str,
        input_files_data: Dict[str, Any],
        input_skills_data: List[str],
        prompt_template: Optional[str] = None,
        max_rounds: int = 1,
    ) -> TrajectoryDict:
        """
        执行单个任务，支持验证循环。

        Args:
            task_id: 任务标识符
            task_description: 任务描述
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            prompt_template: 验证提示词模板
            max_rounds: 最大轮数（>1 时启用验证循环）

        Returns:
            完整轨迹字典:
            {
                "task_id": str,
                "task_description": str,
                "final_output": str,
                "total_rounds": int,
                "is_completed": bool,
                "messages": List[Dict],
                "files_created": List[str],
                "errors": List[str],
                "metadata": Dict,
            }
        """
        self.logger.info(f"开始任务：{task_id}, 描述长度={len(task_description)}")

        if not task_description or not task_description.strip():
            self.logger.error(f"task_description 为空！task_id={task_id}")
            raise Exception("task_description 为空")

        workspace_path = self._get_workspace_path(task_id)
        round_num = 0
        feedback = ""

        # 累积文件创建和错误信息（跨轮次累积）
        all_files_created: List[str] = []
        all_errors: List[str] = []
        all_final_outputs: List[str] = []  # 记录每轮的 final_output
        all_feedbacks: List[str] = []  # 记录每轮的历史反馈
        final_output = ""
        is_completed = False
        messages: List[Dict] = []  # 最后一轮的消息（子类返回全量）

        for retry_attempt in range(self.max_retries):
            try:
                with self._manage_execution_context(
                    workspace_path, input_files_data, input_skills_data, task_id
                ) as initial_file_names:
                    initial_file_names = initial_file_names or []
                    execution_start_time = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S CST"
                    )

                    while round_num < max_rounds:
                        round_num += 1
                        self.logger.info(f"[轮次 {round_num}/{max_rounds}] 执行任务...")

                        # 构建本轮用户输入
                        if round_num == 1:
                            user_input = task_description
                        else:
                            user_input = feedback

                        # 发送查询（子类返回格式化后的轨迹字典）
                        round_result = self._send_query(
                            workspace_path,
                            user_input,
                            add_assets_info=(round_num == 1),
                            current_time=execution_start_time,
                        )

                        if not round_result or not isinstance(round_result, dict):
                            self.logger.warning(f"[轮次 {round_num}] 未找到助手消息")
                            raise Exception("未找到助手消息")

                        # 累积本轮结果
                        messages = round_result.get("messages", [])  # 子类返回全量消息
                        all_files_created.extend(round_result.get("files_created", []))
                        all_errors.extend(round_result.get("errors", []))
                        final_output = round_result.get("final_output", "")
                        all_final_outputs.append(final_output)  # 记录本轮输出

                        # 验证阶段
                        if max_rounds > 1:
                            self.logger.info(f"[轮次 {round_num}] 进行验证...")
                            is_completed, feedback = self._verify_with_external_llm(
                                task_description,
                                all_feedbacks,  # 传递所有历史反馈
                                all_final_outputs,  # 传递所有历史输出
                                prompt_template,
                                workspace_path,
                                initial_file_names,
                            )

                            all_feedbacks.append(feedback)  # 记录本轮反馈

                            if is_completed:
                                self.logger.info(
                                    f"[轮次 {round_num}] 验证通过，任务完成"
                                )
                                break
                            else:
                                self.logger.info(
                                    f"[轮次 {round_num}] 验证未通过，继续迭代：{feedback}"
                                )
                        else:
                            # 单轮模式，直接完成
                            is_completed = True
                            break

                    # 返回完整轨迹字典
                    return {
                        "task_id": task_id,
                        "task_description": task_description,
                        "final_output": final_output,
                        "total_rounds": round_num,
                        "is_completed": is_completed,
                        "messages": messages,
                        "files_created": all_files_created,
                        "errors": all_errors,
                        "metadata": {
                            "max_retries": self.max_retries,
                            "retry_attempt": retry_attempt,
                        },
                    }

            except Exception as e:
                self.logger.error(
                    f"[重试 {retry_attempt + 1}/{self.max_retries}] 任务失败：{e}"
                )
                if retry_attempt < self.max_retries - 1:
                    continue
                # 最后一次重试失败，返回错误轨迹
                return {
                    "task_id": task_id,
                    "task_description": task_description,
                    "final_output": "",
                    "total_rounds": round_num,
                    "is_completed": False,
                    "messages": messages,
                    "files_created": all_files_created,
                    "errors": [str(e)],
                    "metadata": {
                        "max_retries": self.max_retries,
                        "retry_attempt": retry_attempt,
                    },
                }

        # 理论上不会到这里
        return {
            "task_id": task_id,
            "task_description": task_description,
            "final_output": "",
            "total_rounds": round_num,
            "is_completed": False,
            "messages": [],
            "files_created": [],
            "errors": ["未知错误"],
            "metadata": {},
        }

    # =========================================================================
    # 公共接口 - 父类提供完整实现
    # =========================================================================

    def generate_from_input(
        self,
        user_inputs: List[str],
        input_files_data: List[Dict[str, Any]],
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

        total = len(user_inputs)
        completed = 0
        failed = 0
        results: List[Dict[str, Any]] = [{} for _ in range(total)]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for i, question in enumerate(user_inputs):
                if not question or not question.strip():
                    self.logger.warning(f"任务 {i}: user_inputs[{i}] 为空字符串，跳过")
                    results[i] = {
                        "task_id": f"task-{i}",
                        "task_description": question,
                        "final_output": "",
                        "total_rounds": 0,
                        "is_completed": False,
                        "messages": [],
                        "files_created": [],
                        "errors": ["user_inputs 为空"],
                        "metadata": {},
                    }
                    continue

                use_max_rounds = max_verification_rounds if enable_verification else 1

                future = executor.submit(
                    self._execute_single_task_with_verification,
                    f"task-{i}",
                    question,
                    input_files_data[i],
                    input_skills_data[i],
                    verification_prompt_template,
                    use_max_rounds,
                )
                futures[future] = i

            with tqdm(total=total, desc="处理请求", unit="task") as pbar:
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        trajectory = future.result()
                        results[idx] = trajectory
                        completed += 1
                    except Exception:
                        self.logger.exception(f"[任务 {idx + 1}/{total}] 失败")
                        results[idx] = {
                            "task_id": f"task-{idx}",
                            "task_description": user_inputs[idx],
                            "final_output": "",
                            "total_rounds": 0,
                            "is_completed": False,
                            "messages": [],
                            "files_created": [],
                            "errors": ["执行失败"],
                            "metadata": {},
                        }
                        failed += 1
                    pbar.update(1)

        self.logger.info(f"处理完成：成功 {completed}, 失败 {failed}, 总计 {total}")
        return results  # type: ignore
