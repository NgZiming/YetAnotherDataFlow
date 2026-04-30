from __future__ import annotations

import json
import random
import shutil
import traceback
import time

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import re
import requests

from typing_extensions import TypedDict
from tqdm import tqdm

from dataflow.logger import get_logger
from dataflow.utils.generate_binary_files import generate_file

# =========================================================================
# 轨迹字典类型定义(在类定义后,避免循环导入)
# =========================================================================


class MessageDict(TypedDict, total=True):
    """单条消息的字典格式。"""

    round: int  # 所属轮次
    role: str  # "system" | "user" | "assistant" | "tool" | "toolResult"
    content: str  # 消息内容
    thought: Optional[str]  # 思考内容(如有)
    tool_calls: List[Dict]  # 工具调用列表(如有)
    tool_results: List[Dict]  # 工具结果列表(如有)
    id: Optional[str]  # 消息 ID(用于拓扑)
    parentId: Optional[str]  # 父消息 ID(用于拓扑)
    session_id: Optional[str]  # 所属 session ID


class TrajectoryDict(TypedDict, total=True):
    """
    标准轨迹字典格式(子类格式化结果时使用此类型)。
    参考 OpenClawTrajectorySimplifierOperator 的字段保留策略。
    """

    task_id: str  # 任务 ID
    final_output: str  # 最终输出文本
    total_rounds: int  # 总轮数
    is_completed: bool  # 是否通过验证完成
    messages: List[MessageDict]  # 消息列表(按时间顺序)
    metadata: Dict[str, Any]  # 元数据(子类可扩展)


# =========================================================================
# Base Classes
# =========================================================================


class LLMServingABC(ABC):
    """Abstract base class for data generators. Which may be used to generate data from a model or API. Called by operators"""

    @abstractmethod
    def generate_from_input(
        self, user_inputs: List[str], system_prompt: str
    ) -> List[str]:
        """
        Generate data from input.
        input: List[str], the input of the generator
        """
        pass

    @abstractmethod
    def start_serving(self):
        """
        Cleanup the generator and garbage collect all GPU/CPU memory.
        """
        pass

    @abstractmethod
    def cleanup(self):
        """
        Cleanup the generator and garbage collect all GPU/CPU memory.
        """
        pass

    def load_model(self, model_name_or_path: str, **kwargs: Any):
        """
        Load the model from the given path.
        This method is optional and can be overridden by subclasses if needed.
        """
        raise NotImplementedError("This method should be implemented by subclasses.")


"""
AgentServingABC - Agent 能力流程抽象基类

提供复杂 Agent 能力的统一流程抽象,包括:
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
    2. 执行上下文管理(session/agent 创建)
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
        user_simulator_api_url: str,
        user_simulator_api_key: Optional[str] = None,
        user_simulator_client_params: Optional[Dict[str, Any]] = None,
        max_workers: int = 4,
        max_retries: int = 3,
        timeout: int = 300,
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
        """
        self.logger = get_logger()
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.timeout = timeout

        # 用户模拟器配置（不再是"验证"，而是"用户模拟"）
        self._user_simulator_api_url = user_simulator_api_url
        self._user_simulator_api_key = user_simulator_api_key
        self._user_simulator_client_params = {
            "model": "/data/share/models/Qwen3.5-122B-A10B/",
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

        if user_simulator_client_params:
            self._user_simulator_client_params.update(user_simulator_client_params)

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
        准备执行上下文(session/agent 创建、文件准备等)。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            task_id: 任务 ID(用于动态创建/选择 agent)

        Returns:
            路径映射字典 {原始路径:实际生成路径}(可选),用于后续文件增量检测时排除
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
        发送查询并获取响应(子类内部负责格式化轨迹)。

        Args:
            workspace_path: workspace 路径
            query: 用户查询
            current_time: 当前时间字符串,如果为 None 则使用默认时间

        Returns:
            标准轨迹字典(子类内部格式化):
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
        管理完整的执行上下文生命周期(contextmanager 包装器)。

        确保:无论成功失败都会清理资源。

        Args:
            workspace_path: workspace 路径
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            task_id: 任务 ID(用于匹配对应的 agent)

        Yields:
            路径映射字典 {原始路径:实际生成路径}(可选)
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
            input_files_data: 文件内容数据(key 为原始文件路径)
            input_skills_data: skill 名称列表
            skill_base_dir: Skill 基础目录（必需）

        Returns:
            文件路径映射字典 {原始路径:实际生成路径}
        """
        path_mapping = {}

        # 准备文件到 assets 目录
        if input_files_data:
            assets_dir = workspace_path / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)

            for original_path, content_data in input_files_data.items():
                if not content_data or not isinstance(content_data, dict):
                    continue

                # 使用原始路径的文件名作为实际文件名
                filename = Path(original_path).name
                actual_path = str(assets_dir / filename)

                generate_file(
                    {"filename": filename, "content": content_data},
                    str(assets_dir),
                )
                path_mapping[original_path] = actual_path
                self.logger.debug(f"生成文件:{filename} -> {actual_path}")

        # 准备 skills 到 skills 目录
        if input_skills_data:
            skills_dir = workspace_path / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)

            for skill_name in input_skills_data:
                # skill_base_dir 现在是必需的，直接拼接
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
            path_mapping: 路径映射 {原始路径:实际路径}
            workspace_path: agent 的 workspace 路径(用于处理 /workspace/ 到实际 workspace 的映射)

        Returns:
            替换后的文本
        """
        if not path_mapping and not workspace_path:
            return text

        result = text

        # 1. 先应用 path_mapping 中的精确替换 (按长度降序排序, 避免部分匹配)
        if path_mapping:
            for original_path, actual_path in sorted(
                path_mapping.items(), key=lambda x: len(x[0]), reverse=True
            ):
                result = result.replace(original_path, actual_path)

        # 2. 处理 /workspace/ 前缀的通用映射
        # 只要出现 /workspace/xxx，都映射到真实的 workspace_path/xxx
        if workspace_path:
            # 匹配 /workspace/ 及其后面直到空白或引号的部分
            workspace_pattern = r'/workspace/([^\s"\'>]+)'

            def replace_workspace(match):
                relative_path = match.group(1)
                # 构建绝对路径并规范化 (解决 ../ 等问题)
                actual_path = (workspace_path / relative_path).resolve()
                return str(actual_path)

            result = re.sub(workspace_pattern, replace_workspace, result)

            # 额外处理常见的 ~/ 缩写 (如果出现在路径上下文中)
            # 简单处理：将 ~/ 替换为 workspace_path (仅当它像个路径时)
            result = re.sub(
                r'(?<=[\s"\'\)])~/([^\s"\'>]+)',
                lambda m: str((workspace_path / m.group(1)).resolve()),
                result,
            )

        return result

    def _get_new_file_contents(
        self,
        workspace_path: Path,
        path_mapping: Dict[str, str],
        max_total_len: int = 8000,  # 新增：总内容长度限制
        summary_mode: bool = True,  # 新增：是否使用总结模式
        use_llm_summary: bool = False,  # 新增：是否使用 LLM 总结（需要额外 LLM 调用）
    ) -> str:
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
            return ""

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
        EXCLUDED_DIRS = {"skills", "sessions", "agent"}

        # 从 path_mapping 中提取实际生成的文件名集合
        initial_files_set = set(Path(p).name for p in path_mapping.values())
        file_contents = []
        total_len = 0

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

                if summary_mode:
                    # 总结模式：只发送文件大纲 + 首尾总结
                    file_len = len(content)
                    if file_len <= 2000:
                        # 小文件：发送完整内容
                        summary = content
                        actual_len = file_len
                    elif use_llm_summary:
                        # 🔥 使用 LLM 总结大文件
                        summary = self._summarize_file_with_llm(str(rel_path), content)
                        actual_len = len(summary)
                    else:
                        # 机械总结：发送首尾各 500 字符 + 中间摘要
                        head = content[:500]
                        tail = content[-500:]
                        summary = f"{head}\n\n... [内容被截断：文件总长度 {file_len} 字符，中间内容已省略] ...\n\n{tail}"
                        actual_len = 1000 + 50  # 估算长度
                else:
                    # 完整模式：截断单个文件
                    if len(content) > 10000:
                        content = content[:10000] + "\n...(内容被截断)"
                    summary = content
                    actual_len = len(content)

                # 检查是否超过总长度限制
                if total_len + actual_len > max_total_len and total_len > 0:
                    # 添加截断提示
                    file_contents.append(
                        f"\n... [后续文件已省略：总长度已达 {total_len} 字符，超过限制 {max_total_len}] ..."
                    )
                    break

                # 使用相对路径作为标识，方便区分不同目录下的同名文件
                if summary_mode and len(content) > 2000:
                    file_contents.append(
                        f"【{rel_path}】（文件大小：{file_len} 字符，已总结）\n{summary}\n"
                    )
                else:
                    file_contents.append(f"【{rel_path}】\n{summary}\n")

                total_len += actual_len + len(str(rel_path)) + 10

            except Exception:
                self.logger.exception(f"读取文件 {f} 失败")

        result = "\n".join(file_contents) if file_contents else ""

        # 记录日志
        if summary_mode:
            self.logger.info(
                f"文件总结模式：共 {len(files)} 个文件，总长度 {total_len} 字符"
            )
        else:
            self.logger.info(
                f"完整模式：共 {len(files)} 个文件，总长度 {total_len} 字符"
            )

        return result

    def _call_user_simulator_llm(
        self,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
    ) -> str:
        """
        调用用户模拟器 LLM 的通用方法。

        Args:
            prompt: 提示词内容
            max_tokens: 最大生成 token 数（默认使用配置值）
            temperature: 生成温度（默认使用配置值）
            timeout: 超时时间（秒，默认使用配置值）

        Returns:
            LLM 生成的文本内容

        Raises:
            Exception: 当请求失败时抛出异常
        """
        # 构建请求 payload
        payload = {
            "model": self._user_simulator_client_params.get("model", "default"),
            "messages": [{"role": "user", "content": prompt}],
        }

        # 可选参数
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        # 合并其他配置参数
        payload.update(self._user_simulator_client_params)

        # 设置请求头
        headers = {"Content-Type": "application/json"}
        if self._user_simulator_api_key:
            headers["Authorization"] = f"Bearer {self._user_simulator_api_key}"

        # 设置超时
        request_timeout = timeout or self._user_simulator_client_params.get(
            "read_timeout", 600
        )

        try:
            self.logger.info(
                f"向用户模拟器 LLM 发起请求 (prompt 长度={len(prompt)}字符)"
            )

            response = requests.post(
                self._user_simulator_api_url,
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )

            if response.status_code != 200:
                self.logger.error(
                    f"LLM 请求失败：status={response.status_code}, body={response.text[:500]}"
                )
                raise Exception(f"LLM 请求失败：status={response.status_code}")

            result = response.json()
            llm_output = (
                result.get("choices", [{}])[0].get("message", {}).get("content", "")
            )

            self.logger.info(
                f"LLM 调用完成，输出长度={len(llm_output)}字符"
                if llm_output
                else "LLM 调用完成，无输出"
            )

            return llm_output

        except requests.exceptions.ConnectTimeout:
            self.logger.exception("LLM 连接超时")
            raise
        except requests.exceptions.ReadTimeout:
            self.logger.exception("LLM 读取超时")
            raise
        except Exception:
            self.logger.exception("LLM 调用失败")
            raise

    def _summarize_file_with_llm(
        self,
        file_path: str,
        content: str,
        max_summary_len: int = 1000,
    ) -> str:
        """
        使用 LLM 总结文件内容。

        Args:
            file_path: 文件路径（用于提示词）
            content: 文件完整内容
            max_summary_len: 总结的最大长度

        Returns:
            总结后的内容
        """
        # 如果文件内容已经很短，直接返回
        if len(content) <= 2000:
            return content

        # 构建总结提示词
        summary_prompt = f"""请总结以下文件的核心内容，要求：
1. 用简洁的语言概括文件的主要信息
2. 保留关键数据、结论和重要细节
3. 忽略冗余的格式和重复内容
4. 总结长度控制在 {max_summary_len} 字符以内

文件路径：{file_path}
文件长度：{len(content)} 字符

---

文件内容：
{content[:10000]}  # 如果文件太长，先截取前 10000 字符用于总结

---

请输出总结内容："""

        try:
            # 调用 LLM 进行总结
            summary = self._call_user_simulator_llm(
                prompt=summary_prompt,
                max_tokens=1024,
                temperature=0.3,  # 较低的 temperature，确保总结准确
                timeout=60,  # 总结请求超时时间较短
            )

            # 限制总结长度
            if len(summary) > max_summary_len:
                summary = summary[:max_summary_len] + "..."
            return summary

        except Exception as e:
            self.logger.warning(f"LLM 总结异常：{e}, 回退到机械总结")
            # 回退到机械总结
            head = content[:500]
            tail = content[-500:]
            return f"{head}\n\n... [内容被截断：文件总长度 {len(content)} 字符，中间内容已省略] ...\n\n{tail}"

    def _mock_user_with_external_llm(
        self,
        feedbacks: List[str],
        agent_outputs: List[str],
        prompt_template: str,
        workspace_path: Path,
        path_mapping: Dict[str, str],
    ) -> tuple[bool, str]:
        """
        使用外部 LLM 模拟用户，生成下一轮对话的反馈。

        Args:
            feedbacks: 之前每一轮的反馈意见
            agent_outputs: Agent 每一轮的输出
            prompt_template: 用户模拟提示词模板（已包含 task_description/milestones/retrieval_points/dialogue_scripts 等信息）
            workspace_path: workspace 路径
            path_mapping: 路径映射 {原始路径：实际生成路径}

        Returns:
            (是否完成，用户模拟器生成的下一轮反馈消息)
        """
        # 获取新增文件内容（使用 LLM 总结模式，智能压缩大文件）
        file_contents_str = self._get_new_file_contents(
            workspace_path,
            path_mapping,
            summary_mode=True,
            use_llm_summary=True,
        )

        # 使用 str.replace 逐个替换占位符，避免 .format() 因缺失占位符而报错
        user_sim_prompt = self._replace_file_paths_in_text(
            prompt_template,
            path_mapping,
            workspace_path,
        )
        user_sim_prompt = user_sim_prompt.replace(
            "{agent_outputs}",
            json.dumps(agent_outputs, ensure_ascii=False),
        )
        user_sim_prompt = user_sim_prompt.replace(
            "{feedbacks}",
            json.dumps(feedbacks, ensure_ascii=False),
        )
        user_sim_prompt = user_sim_prompt.replace("{file_contents}", file_contents_str)

        try:
            # 使用统一的 LLM 调用方法
            user_sim_output = self._call_user_simulator_llm(
                prompt=user_sim_prompt,
                timeout=self._user_simulator_client_params.get("read_timeout", 600),
            )

            self.logger.info(
                f"用户模拟完成，输出:{user_sim_output[:100]}..."
                if user_sim_output
                else "用户模拟完成，无输出"
            )

            return self._parse_user_result(user_sim_output)
        except requests.exceptions.ConnectTimeout:
            self.logger.exception("用户模拟器 LLM 连接超时")
            raise
        except requests.exceptions.ReadTimeout:
            self.logger.exception("用户模拟器 LLM 读取超时")
            raise
        except Exception:
            self.logger.exception("用户模拟器 LLM 调用失败")
            raise

    def _parse_user_result(self, user_output: str) -> tuple[bool, str]:
        """
        解析验证结果。支持 JSON 格式解析。

        Args:
            verify_output: LLM 验证输出

        Returns:
            (是否完成，反馈信息)

        Raises:
            Exception: 当反馈为空或格式不正确时
        """
        # 尝试解析为 JSON
        # 首先去除可能包裹的 Markdown 代码块
        json_str = user_output.strip()
        if json_str.startswith("```json"):
            json_str = json_str[7:]
        if json_str.startswith("```"):
            json_str = json_str[3:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()

        data = json.loads(json_str)

        judgment = data.get("judgment", "").lower()
        feedback = data.get("feedback", "")

        if not feedback:
            raise ValueError("JSON 中缺失 feedback 字段")

        # 判断完成状态 (completed 为 True, 其他为 False)
        is_completed = judgment == "completed"

        # 处理 aborted 状态: 如果是 aborted，我们可以通过在 feedback 中标记或通过
        # 抛出特定异常/返回特殊标志来告知外层中断。
        # 在目前的 API 签名 (bool, str) 下，aborted 同样返回 False，
        # 但通过 feedback 让 Agent 知道无需再试。
        if judgment == "aborted":
            self.logger.info("验证 LLM 判定任务为 aborted (不可抗力/死循环)")
            # 我们可以通过在 feedback 头部添加特殊标记，让 _execute_single_task_with_verification 能够识别并直接 break
            feedback = f"[ABORTED] {feedback}"

        return is_completed, feedback

    def _execute_single_task_with_verification(
        self,
        task_id: str,
        input_files_data: Dict[str, Any],
        input_skills_data: List[str],
        prompt_template: str,
        max_rounds: int = 1,
    ) -> TrajectoryDict:
        """
        执行单个任务，支持验证循环。

        Args:
            task_id: 任务标识符
            input_files_data: 文件内容数据
            input_skills_data: skill 路径列表
            prompt_template: 用户模拟提示词模板（已包含 task_description/milestones/retrieval_points/dialogue_scripts 等信息）
            max_rounds: 最大轮数 (>1 时启用验证循环)

        Returns:
            完整轨迹字典:
            {
                "task_id": str,
                "final_output": str,
                "total_rounds": int,
                "is_completed": bool,
                "messages": List[Dict],
                "files_created": List[str],
                "errors": List[str],
                "metadata": Dict,
            }
        """
        self.logger.info(f"开始任务:{task_id}")

        # ========== 新增：随机等待，避免大量 agent 同时创建 ==========
        # 随机等待 1-3 秒，分散任务启动时间，减少 agent 创建时的锁竞争
        random_delay = random.uniform(1.0, 3.0)
        self.logger.debug(f"任务 {task_id} 随机等待 {random_delay:.2f} 秒后启动")
        time.sleep(random_delay)
        # =========================================================

        # 状态变量初始化
        workspace_path = self._get_workspace_path(task_id)

        for retry_attempt in range(self.max_retries):
            # 在 retry 循环内部初始化所有状态变量，确保每次重试都是从第一轮(原始任务)开始
            round_num = 0
            conversation = ""
            all_final_outputs: List[str] = []  # 记录每轮的 final_output
            all_feedbacks: List[str] = []  # 记录每轮的历史反馈
            final_output = ""
            is_completed = False
            messages: List[MessageDict] = []  # 最后一轮的消息(子类返回全量)

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

                        is_completed, conversation = self._mock_user_with_external_llm(
                            all_feedbacks,
                            all_final_outputs,
                            prompt_template,
                            workspace_path,
                            path_mapping,
                        )
                        if (
                            is_completed
                            or conversation.startswith("[ABORTED]")
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
                        # 发送查询 (子类返回格式化后的轨迹字典)
                        round_result = self._send_query(
                            workspace_path,
                            conversation,
                            current_time=execution_start_time,
                        )

                        # 累积本轮结果
                        messages: list[MessageDict] = round_result.get("messages", [])
                        final_output = round_result.get("final_output", "")
                        all_final_outputs.append(final_output)  # 记录本轮输出
            except Exception:
                self.logger.exception(
                    f"[重试 {retry_attempt + 1}/{self.max_retries}] 任务失败"
                )
                if retry_attempt < self.max_retries - 1:
                    time.sleep(5 * (retry_attempt + 1))  # 指数退避:5s, 10s
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

    # =========================================================================
    # 公共接口 - 父类提供完整实现
    # =========================================================================

    def generate_from_input(
        self,
        user_inputs: List[str],
        input_files_data: List[Dict[str, Any]],
        input_skills_data: List[List[str]],
        max_rounds: int = 1,
    ) -> List[TrajectoryDict]:
        """
        生成轨迹字典列表 (并发执行)。

        Args:
            user_inputs: 用户输入列表（每行一个任务的初始对话，由用户模拟器生成）
            input_files_data: 文件内容数据列表
            input_skills_data: skill 路径列表
            max_rounds: 最大对话轮数
            milestones: 任务规划列表（每行一个，可选）
            retrieval_points: 预期推理锚点列表（每行一个，可选）
            user_dialogue_scripts: 完整对话脚本列表（每行一个，可选）

        Returns:
            List[TrajectoryDict] - 轨迹字典列表
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

        self.logger.info(f"开始处理 {len(user_inputs)} 个请求，最大轮数={max_rounds}")

        total = len(user_inputs)
        completed = 0
        failed = 0
        results: List[Optional[TrajectoryDict]] = [None for _ in range(total)]

        def execute_task(i: int) -> Optional[TrajectoryDict]:
            try:
                trajectory = self._execute_single_task_with_verification(
                    f"task-{i}",
                    input_files_data[i],
                    input_skills_data[i],
                    user_inputs[i],
                    max_rounds,
                )
                return trajectory
            except Exception:
                self.logger.exception(f"任务 {i} 执行失败")
                return None

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(execute_task, i): i for i in range(total)}

            with tqdm(total=total, desc="处理请求", unit="task") as pbar:
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        trajectory = future.result()
                        results[idx] = trajectory
                        completed += 1
                    except Exception:
                        failed += 1
                    pbar.update(1)

        self.logger.info(f"处理完成：成功 {completed}, 失败 {failed}, 总计 {total}")
        return results  # type: ignore
