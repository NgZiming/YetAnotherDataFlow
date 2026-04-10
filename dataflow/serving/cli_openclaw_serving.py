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
import subprocess
import time
import shutil
import os
from contextlib import contextmanager

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any, List

from tqdm import tqdm
from openai import OpenAI

from dataflow.core import LLMServingABC
from dataflow.logger import get_logger

# 导入二进制文件生成模块
from dataflow.utils.generate_binary_files import generate_file

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

    # 持续等待直到找到 session 文件 (最多 60 秒)
    start_time = time.time()
    timeout = 60  # 60 秒超时

    while time.time() - start_time < timeout:
        if sessions_dir.exists():
            candidates = list(sessions_dir.rglob("*.jsonl"))
            if candidates:
                # 按修改时间排序，最新的在前
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

    注意：此函数假设文件和 skills 已经通过 _prepare_and_create_session 准备好，
    或者在当前 session 中已经存在。

    Args:
        agent_id: Agent 标识符
        user_query: 用户查询内容
        timeout: 超时时间 (秒)
        logger: 日志对象
        add_assets_info: 是否添加文件/skills 信息到 prompt，默认 True(仅初次请求需要)

    Returns:
        所有消息列表 (按时间戳排序)

    Raises:
        Exception: 执行失败时抛出异常
    """
    # 添加文件/技能路径到 prompt(如果存在)
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

    # 执行查询 (OpenClaw 会自动使用当前 session)
    cmd = ["openclaw", "agent", "--agent", agent_id, "--local", "-m", query]

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise Exception(result.stderr)

    logger.info(f"请求完成:{result.stdout[:50]}...")

    # 解析输出 (提取所有消息)
    transcript_paths = _resolve_transcript_paths(agent_id)
    messages = []
    for transcript_path in transcript_paths:
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            messages.append(json.loads(line))
    messages = sorted(messages, key=lambda x: x["timestamp"])

    return messages


# ============================================================================
# 响应处理函数
# ============================================================================


# ============================================================================
# Session 上下文管理
# ============================================================================


@contextmanager
def _prepare_and_create_session(
    agent_id: str,
    timeout: int,
    logger: Any,
    input_files_data: Dict,
    input_skills_data: List[str],
):
    """
    Session 上下文管理器：创建新 session 并准备文件/skills，退出时清理。

    Args:
        agent_id: Agent 标识符
        timeout: 超时时间（秒）
        logger: 日志对象
        input_files_data: 文件内容数据
        input_skills_data: Skill 绝对路径列表

    Yields:
        tuple: (new_file_paths, new_skill_paths) - 生成的文件和新拷贝的 skill 路径
    """
    workspace_dir = _workspace_dir(agent_id)
    assets_dir = workspace_dir / "assets"
    skills_dir = workspace_dir / "skills"

    new_file_paths: list[str] = []
    new_skill_paths: list[str] = []

    # 核心配置文件列表
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
        # 清理 assets 和 skills 目录
        for d in [assets_dir, skills_dir]:
            if d.exists():
                try:
                    shutil.rmtree(d)
                except Exception as e:
                    logger.error(f"清空 {d} 失败：{e}")

        # 清理 workspace 中除核心文件外的其他文件
        if workspace_dir.exists():
            for item in workspace_dir.iterdir():
                if item.name in core_files:
                    continue  # 保留核心文件
                if item.is_file():
                    try:
                        item.unlink()
                        logger.debug(f"清理 workspace 文件：{item.name}")
                    except Exception as e:
                        logger.error(f"清理文件失败 {item}: {e}")
                elif item.is_dir():
                    try:
                        shutil.rmtree(item)
                        logger.debug(f"清理 workspace 目录：{item.name}")
                    except Exception as e:
                        logger.error(f"清理目录失败 {item}: {e}")

    try:
        # 生成二进制文件
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

        # 拷贝 skill 目录
        skills_dir.mkdir(parents=True, exist_ok=True)
        for skill_path in input_skills_data:
            src_path = Path(skill_path)
            if not src_path.exists() or not src_path.is_dir():
                raise Exception(f"Skill 路径不存在：{skill_path}")
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
            timeout=timeout,
            check=False,
        )
        if new_result.returncode != 0:
            raise RuntimeError(f"/new 命令失败：{new_result.stderr}")

        yield new_file_paths, new_skill_paths

    finally:
        _cleanup()


# ============================================================================
# CLIOpenClawServing 类
# ============================================================================


class CLIOpenClawServing(LLMServingABC):
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
        # 验证用 LLM 参数
        verification_api_key: Optional[str] = None,
        verification_base_url: Optional[str] = None,
        verification_client_params: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化 CLIOpenClawServing。

        Args:
            agent_id: Agent 标识符，默认 "main"
            model: 模型名称，如果 agent 不存在且 create_if_missing=True 则用于创建
            timeout: 单次查询超时时间（秒），None 表示不设置超时，默认 None
            create_if_missing: 如果 agent 不存在是否自动创建，默认 True
            max_workers: 并发 worker 数量，默认 4
            max_retries: 请求失败时的最大重试次数，默认 3
            verification_api_key: 验证用 API key，从 OPENAI_API_KEY 环境变量读取
            verification_base_url: 验证用 API base URL，从 OPENAI_BASE_URL 环境变量读取
            verification_client_params: 验证用 LLM 调用参数字典，支持：
                - model: 模型名称，默认 gpt-4o-mini
                - max_completion_tokens: 最大生成 token，默认 16384
                - read_timeout: 读取超时（秒），默认 600
                - temperature: 默认 0.7
                - top_p: 默认 0.8
                - top_k: 默认 20
                - min_p: 默认 0.0
                - presence_penalty: 默认 1.5
                - repetition_penalty: 默认 1.0
                - chat_template_kwargs: 如 {"enable_thinking": false}
        """
        self.logger = get_logger()

        self.agent_id = agent_id
        self.model: str = model or "vllm//data/share/models/Qwen3.5-122B-A10B/"
        self.timeout = timeout
        self.create_if_missing = create_if_missing
        self.max_workers = max_workers
        self.max_retries = max_retries

        # 验证用 LLM 参数
        self.verification_client_params = {
            "max_completion_tokens": 16384,
            "timeout": 600,
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
            self.verification_client_params.update(verification_client_params)

        api_key = verification_api_key
        base_url = verification_base_url

        if api_key:
            # 从 params 中读取 timeout
            api_timeout = self.verification_client_params.get("timeout", 600)
            self._verification_client = OpenAI(
                api_key=api_key,
                base_url=base_url if base_url else None,
                timeout=api_timeout,
            )
        else:
            self._verification_client = None
            self.logger.warning("未设置 OPENAI_API_KEY，验证功能将不可用")

        self._initialized = False
        self._worker_agents: List[str] = []

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

    def _setup_and_check_worker_agents(self, initial_setup: bool = False) -> None:
        """设置或检查所有 worker agents 的健康状态，重建不可用的。

        Args:
            initial_setup: 如果是 True，只在初始化时设置，不检查现有 agent
        """
        existing = _list_existing_agents()
        new_worker_agents = []

        for i in range(self.max_workers):
            worker_agent_id = f"{self.agent_id}-worker-{i:04}"
            needs_creation = False
            needs_rebuild = False

            # 检查 agent 是否存在
            if worker_agent_id.lower() not in existing:
                needs_creation = True
            elif initial_setup:
                # 初始设置时，如果存在就跳过
                new_worker_agents.append(worker_agent_id)
                continue

            # 如果需要创建或重建
            if needs_creation or needs_rebuild:
                if needs_rebuild:
                    # 删除并重建 agent
                    subprocess.run(
                        ["openclaw", "agents", "delete", "--force", worker_agent_id],
                        check=False,
                    )

                if self.create_if_missing and self.model:
                    self.logger.info(f"正在创建 worker agent: {worker_agent_id}")
                    create_agent(worker_agent_id, self.model)

                    # 等待 agent 注册成功
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

            new_worker_agents.append(worker_agent_id)

        self._worker_agents = new_worker_agents
        if not initial_setup:
            self.logger.info(f"Worker agents 检查完成：{self._worker_agents}")

    def start_serving(self) -> None:
        """启动服务（确保 agent 存在）。"""
        if self._initialized:
            return

        self._ensure_agent()
        self._setup_and_check_worker_agents(initial_setup=True)
        self._initialized = True

    def generate_from_input(
        self,
        user_inputs: List[str],
        input_files_data: List[Dict],
        input_skills_data: List[List[str]],
        enable_verification: bool = False,
        verification_prompt_template: Optional[str] = None,
        max_verification_rounds: int = 3,
    ) -> List[str]:
        """
        生成文本响应（并发执行，每个请求使用独立 worker agent）。

        注意：
        - input_files_data 和 input_skills_data 在 /new 之前处理
        - 每个 worker agent 独立处理一个请求，session 完全隔离

        Args:
            user_inputs: 用户输入列表
            input_files_data: 与 user_inputs 长度相同，每个元素是对应 query 的文件内容数据
                             格式：{filename: content_data}
            input_skills_data: 与 user_inputs 长度相同，每个元素是对应 query 的 skill 绝对路径列表
                              格式：List[str]

        Returns:
            回复列表，长度与输入相同，失败位置为空字符串
        """
        if not self._initialized:
            self.start_serving()

        # 在每次生成前检查并重建不可用的 worker agents
        self._setup_and_check_worker_agents(initial_setup=False)

        if not user_inputs:
            self.logger.warning("user_inputs 为空，直接返回")
            return []

        # 严格长度验证（必需参数，不允许为 None）
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

        self.logger.info(
            f"开始处理 {len(user_inputs)} 个请求，使用 {len(self._worker_agents)} 个 worker agents"
        )

        total = len(user_inputs)
        completed = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for i, question in enumerate(user_inputs):
                # 分配 worker agent（轮询）
                worker_agent_id = self._worker_agents[i % len(self._worker_agents)]
                # 传入对应的文件数据和 skill 数据
                # 统一执行路径
                use_max_rounds = max_verification_rounds if enable_verification else 1
                future = executor.submit(
                    self._execute_single_task_with_verification,
                    worker_agent_id,
                    question,
                    verification_prompt_template,
                    use_max_rounds,
                    input_files_data[i],
                    input_skills_data[i],
                )
                futures[future] = i

                self.logger.debug(f"已提交 {len(futures)} 个任务到线程池")

            results: list[str] = [""] * total

            with tqdm(total=total, desc="处理请求", unit="task") as pbar:
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                        completed += 1
                    except Exception as e:
                        self.logger.error(f"[任务 {idx + 1}/{total}] 失败：{e}")
                        results[idx] = ""
                        failed += 1
                    pbar.update(1)

        self.logger.info(f"处理完成：成功 {completed}, 失败 {failed}, 总计 {total}")
        return results

    def generate_embedding_from_input(self, texts: List[str]) -> List[List[float]]:
        """生成嵌入向量（CLI 模式不支持，返回空向量）。"""
        self.logger.warning("CLI 模式不支持 embedding，返回空向量")
        return [[] for _ in texts]

    def _verify_with_external_llm(
        self,
        task_description: str,
        agent_output: str,
        prompt_template: Optional[str],
    ) -> tuple[bool, str]:
        """使用外部 LLM 进行验证。

        Args:
            task_description: 任务描述
            agent_output: Agent 输出
            prompt_template: 验证提示词模板

        Returns:
            (是否完成，反馈信息)
        """
        if not self._verification_client:
            self.logger.warning("验证用 OpenAI 客户端未初始化，跳过验证")
            return True, ""  # 验证失败默认认为完成，避免死循环

        # 构建验证提示词
        if not prompt_template:
            verification_prompt = (
                f"请评估以下任务是否完成:\n\n"
                f"任务:{task_description}\n\n"
                f"Agent 输出:\n{agent_output}\n\n"
                f"请返回:\n判断:completed/incomplete\n反馈:(如果未完成，给出具体的改进建议)"
            )
        else:
            verification_prompt = prompt_template.format(
                task_description=task_description,
                agent_output=agent_output,
            )

        try:
            response = self._verification_client.chat.completions.create(
                **self.verification_client_params
            )
            verify_output = response.choices[0].message.content
            return self._parse_verification_result(verify_output)
        except Exception as e:
            self.logger.error(f"验证 LLM 调用失败:{e}")
            # 验证失败时默认认为完成，避免死循环
            return True, ""

    def _parse_verification_result(self, verify_output: str) -> tuple[bool, str]:
        """解析验证结果。

        Args:
            verify_output: LLM 验证输出

        Returns:
            (是否完成，反馈信息)
        """
        verify_output = verify_output.lower()

        # 检查是否完成
        is_completed = (
            "completed" in verify_output and "incomplete" not in verify_output
        )

        # 提取反馈信息
        feedback = ""
        if "反馈:" in verify_output:
            feedback = verify_output.split("反馈:")[-1].strip()
        elif "improvement" in verify_output or "建议" in verify_output:
            feedback = verify_output

        return is_completed, feedback

    def _execute_single_task_with_verification(
        self,
        worker_agent_id: str,
        task_description: str,
        prompt_template: Optional[str],
        max_rounds: int,
        input_files_data: Dict,
        input_skills_data: List[str],
    ) -> str:
        """
        执行单个任务，支持验证循环（带整个任务级别的重试）。

        Args:
            worker_agent_id: Worker Agent 标识符
            task_description: 任务描述
            prompt_template: 验证提示词模板
            max_rounds: 最大执行轮数（>1 时启用验证循环）
            input_files_data: 文件内容数据
            input_skills_data: Skill 相对路径列表

        Returns:
            最终输出
        """
        self.logger.info(f"开始任务执行：{task_description[:50]}...")

        round_num = 0
        previous_output = ""
        feedback = ""

        # 对整个任务进行重试
        for retry_attempt in range(self.max_retries):
            try:
                # 使用 contextmanager 管理整个验证循环的 session 生命周期
                with _prepare_and_create_session(
                    worker_agent_id,
                    self.timeout,
                    self.logger,
                    input_files_data,
                    input_skills_data,
                ):
                    while round_num < max_rounds:
                        round_num += 1
                        self.logger.info(f"[轮次 {round_num}/{max_rounds}] 执行任务...")

                        # 构建本轮用户输入
                        if round_num == 1:
                            user_input = task_description
                        else:
                            # 后续轮次：只用 feedback，更像真人的回答
                            user_input = feedback

                        # 执行任务（发送查询到 session）
                        # 仅在首轮添加文件/skills 信息，后续轮次 session 已存在
                        messages = _send_query_to_session(
                            worker_agent_id,
                            user_input,
                            self.timeout,
                            self.logger,
                            add_assets_info=(round_num == 1),
                        )
                        # 从消息列表中提取最后一条助手消息
                        output: str = ""
                        for m in reversed(messages):
                            # 消息结构：m["message"]["role"] 和 m["message"]["content"]
                            if m.get("message", {}).get("role", None) == "assistant":
                                content_list = m.get("message", {}).get("content", [])
                                # content 是列表，提取 text 类型的内容（忽略 thinking）
                                content_parts = []
                                for item in content_list:
                                    if (
                                        isinstance(item, dict)
                                        and item.get("type") == "text"
                                    ):
                                        content_parts.append(item.get("text", ""))
                                output = "\n\n".join(content_parts)
                                break

                        if not output:
                            self.logger.warning(f"[轮次 {round_num}] 未找到助手消息")
                            raise Exception("未找到助手消息")

                        previous_output = output

                        # 验证阶段（max_rounds=1 时跳过）
                        if max_rounds > 1:
                            self.logger.info(f"[轮次 {round_num}] 进行验证...")
                            is_completed, feedback = self._verify_with_external_llm(
                                task_description,
                                output,
                                prompt_template,
                            )

                            if is_completed:
                                self.logger.info(f"[轮次 {round_num}] 任务完成！")
                                break

                            self.logger.info(
                                f"[轮次 {round_num}] 任务未完成，反馈：{feedback[:50]}..."
                            )

                    # with 块结束时自动清理 workspace
                    break  # 任务成功完成，退出重试循环

            except Exception as e:
                if retry_attempt < self.max_retries - 1:
                    self.logger.warning(
                        f"任务执行失败，重试 {retry_attempt + 1}/{self.max_retries}: {e}"
                    )
                    time.sleep(1.0)  # 等待 1 秒后重试
                else:
                    self.logger.error(f"任务重试 {self.max_retries} 次后仍失败：{e}")
                    raise

        self.logger.info("任务执行结束，session 已清理")
        return json.dumps({"messages": messages}, ensure_ascii=False)

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

    Args:
        agent_id: Agent 标识符，默认 "main"
        model: 模型名称
        max_workers: 并发 worker 数量，默认 4
        max_retries: 请求失败时的最大重试次数，默认 3
        **kwargs: 其他参数（timeout, create_if_missing）
    """
    return CLIOpenClawServing(
        agent_id=agent_id,
        model=model,
        max_workers=max_workers,
        max_retries=max_retries,
        **kwargs,
    )
