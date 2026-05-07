import asyncio

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from dataflow.logger import get_logger
from dataflow.core.agentic import (
    LLMClientABC,
    StepSchema,
    UserStage,
    UserStep,
)

logger = get_logger()

# --- Perception Stage Models (Pydantic) ---


class AgentContext(BaseModel):
    """
    Tracks the agent's ongoing state and reasoning patterns.
    """

    key_actions: List[str] = Field(
        default_factory=list, description="Key actions taken by the agent"
    )
    key_findings: List[str] = Field(
        default_factory=list, description="Important findings discovered by the agent"
    )
    reasoning_pattern: str = Field(
        ..., description="The current reasoning pattern (e.g., initial_exploration)"
    )
    summary: str = Field(..., description="Summary of the agent's current state")


class DialogueContext(BaseModel):
    """
    Captures the user's intent and emotional context for dialogue generation.
    """

    user_intent: str = Field(..., description="The user's primary intent")
    emotional_tone: str = Field(
        default="neutral", description="Emotional tone (e.g., curious, frustrated)"
    )
    key_questions: List[str] = Field(
        default_factory=list, description="Key questions the user is asking"
    )
    implicit_needs: List[str] = Field(
        default_factory=list, description="Implicit needs not explicitly stated"
    )
    summary: str = Field(..., description="Summary of the dialogue context")
    has_history: bool = Field(
        default=False, description="Whether there is dialogue history"
    )


class PerceptionStage(UserStage):
    """
    Perception Stage: Compresses raw data into structured context.
    Prompts are embedded in StepSchema for self-contained configuration.
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        self.llm_config = llm_config or {}

        # ========== Prompts embedded in StepSchema ==========
        self.steps = [
            UserStep(
                name="FileSensor",
                schema=StepSchema(
                    input_keys=["file_contents"],
                    output_key="file_context",
                    prompt_template="""你是一个用户模拟器。分析单个文件，提取其关键信息。

## 输入
- 文件路径：{file_path}
- 文件内容：
```
{file_content}
```

## 任务
根据文件类型提取关键信息：
- **代码文件** (.py, .js, .java 等): 提取函数/类定义、核心逻辑、依赖关系、关键算法
- **配置文件** (.yaml, .json, .toml, .ini 等): 提取配置项、参数设置、环境依赖
- **数据文件** (.csv, .json, .parquet, .txt 等): 提取数据结构、字段含义、数据规模
- **文档文件** (.md, .rst, .txt 等): 提取核心概念、重要说明、使用指南

## 输出要求
- 格式：纯文本总结（不要 JSON）
- 长度：不超过 200 字
- 重点：只提取与任务相关的关键信息

## 输出示例
"这是一个实现用户认证的 Python 模块，包含 login() 和 logout() 两个主要函数，使用 JWT 进行 token 管理，依赖 FastAPI 和 PyJWT 库。""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="AgentSensor",
                schema=StepSchema(
                    input_keys=["file_context", "agent_outputs"],
                    output_key="agent_context",
                    output_type=AgentContext,
                    prompt_template="""你是一个用户模拟器。分析 Agent 的行动轨迹，提取关键行动和发现。

## 输入
- Agent 输出轨迹：{agent_outputs}
- 文件上下文：{file_context}

## 任务
**重要：检查是否为首次对话**
- 如果 agent_outputs 为空或只有系统消息：这是首次对话，Agent 尚未执行任何操作
- 如果 agent_outputs 有内容：分析 Agent 的行动和发现

对于非首次对话：
1. **识别关键行动**: 提取 Agent 执行的重要操作（工具调用、文件创建、API 请求等）
2. **提取关键发现**: 总结 Agent 从搜索/检索中获得的重要信息
3. **识别推理模式**: 判断 Agent 的推理模式（initial_exploration/gap_identification/hypothesis_testing/course_correction）
4. **关联文件信息**: 将 Agent 的发现与文件上下文关联

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown 代码块）
- 必须包含以下字段：
  - key_actions: 关键行动列表
  - key_findings: 关键发现列表
  - reasoning_pattern: 推理模式 (initial_exploration|gap_identification|hypothesis_testing|course_correction)
  - summary: 综合总结，不超过 300 字

## 输出示例（首次对话）
{{
  "key_actions": [],
  "key_findings": [],
  "reasoning_pattern": "initial_exploration",
  "summary": "Agent 尚未开始执行任务，等待用户提出问题。"
}}

## 输出示例（非首次对话）
{{
  "key_actions": ["搜索了 composer X 的音乐风格", "检索了 progressive house 的发展历史"],
  "key_findings": ["发现 composer X 是 electronic music 领域的知名人物", "progressive house 起源于 1990 年代"],
  "reasoning_pattern": "initial_exploration",
  "summary": "Agent 进行了初步探索，搜索了目标作曲家的背景和音乐风格，获得了基础信息。"
}}""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="DialogueSensor",
                schema=StepSchema(
                    input_keys=["feedbacks", "agent_context", "file_context"],
                    output_key="dialogue_context",
                    output_type=DialogueContext,
                    prompt_template="""你是一个用户模拟器。分析用户反馈历史，提取用户意图。

## 输入
- 用户反馈历史：{feedbacks}
- Agent 上下文：{agent_context}
- 文件上下文：{file_context}

## 重要：判断是否有历史对话
- 如果 feedbacks 为空列表 []：没有历史对话，跳过分析，输出空状态
- 如果 feedbacks 非空：分析历史对话，提取用户意图

### 场景 A：没有历史对话（feedbacks 为空）
输出：
{{
  "user_intent": "",
  "emotional_tone": "neutral",
  "key_questions": [],
  "implicit_needs": [],
  "summary": "没有历史对话，等待生成初始问题。",
  "has_history": false
}}

### 场景 B：有历史对话（feedbacks 非空）
任务：基于历史反馈和 Agent 的最新回应，提取用户意图
1. **识别用户意图**: 从反馈中提取用户的真实需求和期望
2. **分析情感倾向**: 判断用户对当前进展的满意度（满意/不满/困惑/急切）
3. **提取关键问题**: 识别用户提出的核心问题和待解决事项
4. **推断潜在需求**: 基于上下文推断用户可能需要的但未明确表达的信息
5. **评估 Agent 回应**: 根据 agent_context 判断 Agent 是否回答了用户的问题

输出示例：
{{
  "user_intent": "用户希望获得更详细的音乐风格分析",
  "emotional_tone": "satisfied",
  "key_questions": ["composer X 的代表作品有哪些？"],
  "implicit_needs": ["希望获得具体的音乐作品推荐"],
  "summary": "用户对 Agent 的初步回答满意，但希望获得更详细的作品信息。",
  "has_history": true
}}

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown 代码块）
- 必须包含以下字段：
  - user_intent: 用户的核心意图描述（无历史对话时为空字符串）
  - emotional_tone: satisfied|dissatisfied|confused|urgent|neutral
  - key_questions: 问题列表
  - implicit_needs: 潜在需求列表
  - summary: 综合总结，不超过 200 字
  - has_history: true|false""",
                ),
                llm_config=self.llm_config,
            ),
        ]

    async def _summarize_file(
        self,
        file_path: str,
        file_content: str,
        prompt_template: str,
        llm_client: LLMClientABC,
    ) -> tuple[str, str]:
        """Summarize a single file."""
        try:
            prompt = prompt_template.format(
                file_path=file_path, file_content=file_content
            )
            summary = await llm_client.generate(prompt, config=self.llm_config)
            return (file_path, summary)
        except Exception as e:
            logger.error(f"Failed to summarize file {file_path}: {e}")
            return (file_path, f"[Error summarizing file: {str(e)}]")

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ):
        logger.info("Entering Perception Stage...")

        # 预先检查所有步骤的输入依赖
        self._check_step_dependencies(self.steps, data_pool, "PerceptionStage")

        # FileSensor: process each file separately with parallel LLM calls
        file_sensor_step = self.steps[0]
        file_contents: Dict[str, str] = data_pool.get("file_contents", {})

        if file_contents:
            logger.info(f"Processing {len(file_contents)} files in parallel...")
            tasks = [
                self._summarize_file(
                    file_path=path,
                    file_content=content,
                    prompt_template=file_sensor_step.schema.prompt_template,
                    llm_client=llm_client,
                )
                for path, content in file_contents.items()
            ]
            results = await asyncio.gather(*tasks)
            file_context = dict(results)
            data_pool[file_sensor_step.schema.output_key] = file_context
        else:
            logger.warning(
                "No file_contents provided, FileSensor will produce empty file_context"
            )
            data_pool[file_sensor_step.schema.output_key] = {}

        # 执行剩余步骤
        for step in self.steps[1:]:
            res = await step.execute(data_pool, global_context, llm_client)
            data_pool[step.schema.output_key] = res["json_resp"]

        for step in self.steps:
            logger.info(f"{data_pool[step.schema.output_key]}")
