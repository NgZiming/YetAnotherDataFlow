import concurrent.futures
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
    is_looping: bool = Field(
        default=False,
        description="Whether the agent is stuck in a behavioral loop (repeating actions without progress)",
    )
    loop_description: Optional[str] = Field(
        default=None,
        description="Description of the repetitive behavior if is_looping is True",
    )
    summary: str = Field(..., description="Summary of the agent's current state")


class DialogueContext(BaseModel):
    """
    Captures the user's intent and emotional context for dialogue generation.
    """

    user_intent: str = Field(..., description="The user's primary intent")
    emotional_tone: str = Field(
        ...,
        description="Emotional tone (must be one of: satisfied, dissatisfied, confused, urgent, neutral)",
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
                    input_keys=["file_contents", "question"],
                    output_key="file_context",
                    prompt_template="""你是一个用户模拟器。分析单个文件，提取其关键信息。

## 输入
- 文件路径：{file_path}
- 文件内容：
```
{file_content}
```
- 用户问题：{question}

## 输入字段说明
- **file_path**: 文件的完整路径，用于识别文件类型和位置
- **file_content**: 文件的原始内容，需要从中提取关键信息
- **question**: 用户的问题/任务描述，用于判断哪些信息是相关的

## 输出字段说明
- **file_context**: 后续步骤（AgentSensor, DialogueSensor）将使用此总结来：
  1. 理解任务相关的代码/配置/数据结构
  2. 判断 Agent 是否访问了正确的文件
  3. 关联 Agent 的发现与文件内容

## 任务
根据文件类型和**用户问题**提取关键信息：
- **代码文件** (.py, .js, .java 等): 提取与问题相关的函数/类定义、核心逻辑、依赖关系
- **配置文件** (.yaml, .json, .toml, .ini 等): 提取与问题相关的配置项、参数设置
- **数据文件** (.csv, .json, .parquet, .txt 等): 提取与问题相关的数据结构、字段含义
- **文档文件** (.md, .rst, .txt 等): 提取与问题相关的核心概念、重要说明

## 提取原则
- **相关性优先**: **只提取与用户问题直接相关的信息**，忽略无关内容
- **简洁性**: 用 1-2 句话概括核心内容
- **可检索性**: 保留关键术语（函数名、类名、配置项），便于后续匹配
- **问题导向**: 思考"用户问这个问题时，最关心文件的哪些部分？"

## 输出要求
- 格式：纯文本总结（不要 JSON）
- 长度：不超过 200 字
- 重点：**紧扣用户问题**，只提取与任务相关的关键信息

## 输出示例
用户问题："如何实现用户认证？"
输出："这是一个实现用户认证的 Python 模块，包含 login() 和 logout() 两个主要函数，使用 JWT 进行 token 管理，依赖 FastAPI 和 PyJWT 库。"

用户问题："composer X 的音乐风格是什么？"
输出："这是一个关于 composer X 的文档，介绍了他的音乐风格（progressive house）、代表作品（如 'Symphony No.1'）、以及他在 electronic music 领域的影响。"""
                    "",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="AgentSensor",
                schema=StepSchema(
                    input_keys=["file_context", "agent_outputs"],
                    output_key="agent_context",
                    output_type=AgentContext,
                    prompt_template="""你是一个用户模拟器。分析 Agent 的回答历史，提取关键行动和发现。

## 输入
- Agent 输出轨迹：{agent_outputs}
- 文件上下文：{file_context}

## 输入字段说明
- **agent_outputs**: Agent 的回答历史列表，每个元素是一行字符串：
  - 格式：`["Agent 回答 1", "Agent 回答 2", "Agent 回答 3", ...]`
  - 内容：Agent 每次对话的输出文本，可能包含工具调用、发现、总结等
  - 顺序：按时间顺序排列，最早的在前

- **file_context**: 之前对文件的总结，用于关联 Agent 的发现与文件内容

## 输出字段说明
- **key_actions**: Agent 执行的关键行动列表
  - **提取方法**: 从 Agent 回答中识别"做了什么"
  - **粒度**: 描述"做了什么"，而不是"调用了什么工具"
  - **示例**: ✅ "搜索了 composer X 的音乐风格"
            ❌ "调用了 search 工具"
  - **数量**: 3-5 个最重要的行动

- **key_findings**: Agent 获得的重要发现列表
  - **来源**: 从 Agent 回答中提取关键信息
  - **重要性**: 只包含对理解任务有帮助的信息
  - **数量**: 3-5 个关键发现

- **reasoning_pattern**: Agent 的推理模式（必须从以下值中选择一个）
  - **initial_exploration**: 初步探索，收集基础信息（如：搜索背景、阅读文档）
  - **gap_identification**: 发现信息缺口，寻找缺失内容（如：发现缺少某个关键数据）
  - **hypothesis_testing**: 验证假设，确认某个猜想（如：测试某个方案是否可行）
  - **course_correction**: 发现错误，调整方向（如：发现之前的理解有误，重新开始）

- **is_looping**: 是否陷入行为死循环（必须选择 true 或 false）
  - **判定标准**:
    - Agent 在连续 3 轮或更多对话中，采取的行动高度相似（例如：反复提供理论分析 -> 道歉 -> 再次提供理论分析）。
    - 尽管 Agent 在尝试，但 key_findings 没有任何实质性的增量更新。
    - 用户已明确要求具体结果（如代码、计划），但 Agent 持续以相同模式回避或重复相同形式的回答。
    - **零增量原则**: 只要行为模式在重复，且在最近 2 轮内没有产生任何能够推动里程碑进度的实质性新发现，即判定为 true。
  - **关键**: 不要将“分步执行”误判为“死循环”，但要极其敏锐地捕捉“原地打转”。

- **loop_description**: 死循环行为描述
  - 如果 is_looping 为 true，请详细描述 Agent 在重复什么行为（例如："Agent 反复提供高层方法论而无法给出具体执行计划，且在用户提醒后依然重复此模式"）。
  - 如果 is_looping 为 false，则为空字符串。

- **summary**: 综合总结（不超过 300 字）
  - 整合 key_actions 和 key_findings
  - 说明 Agent 当前进展和下一步方向
  - 评估是否接近目标

## 任务
**重要：检查是否为首次对话**
- 如果 agent_outputs 为空列表 []：这是首次对话，Agent 尚未执行任何操作
- 如果 agent_outputs 有内容：分析 Agent 的回答和行动

对于非首次对话：
1. **阅读所有回答**: 按顺序阅读 agent_outputs 中的每个字符串
2. **识别关键行动**: 从回答中提取 Agent 执行的重要操作
3. **提取关键发现**: 总结 Agent 获得的重要信息
4. **判断推理模式**: 根据行动序列判断推理模式
5. **关联文件信息**: 将 Agent 的发现与文件上下文关联

## 增量感知原则（重要！）
- **识别增量贡献**: 不要只关注 Agent 是否给出了最终答案，要识别 Agent 在这一轮对话中提供了哪些**新信息**或完成了哪些**中间步骤**。
- **正向引导**: 如果 Agent 正在尝试正确的工具、阅读了正确的文件或提出了合理的假设，即使结果不完整，也应将其记录在 `key_actions` 和 `key_findings` 中。
- **避免过度负面**: 不要将“分步执行”误判为“无进展”。只要有实质性的新发现（即使很小），就应将其视为正向进展。

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown 代码块）
- 只输出纯 JSON，不要任何解释文字
- 必须包含以下字段

## 输出示例（首次对话）
{{
  "key_actions": [],
  "key_findings": [],
  "reasoning_pattern": "initial_exploration",
  "is_looping": false,
  "loop_description": "",
  "summary": "Agent 尚未开始执行任务，等待用户提出问题。"
}}

## 输出示例（非首次对话）
{{
  "key_actions": ["搜索了 composer X 的音乐风格", "检索了 progressive house 的发展历史", "阅读了 composer X 的官方文档"],
  "key_findings": ["发现 composer X 是 electronic music 领域的知名人物", "progressive house 起源于 1990 年代", "composer X 的作品融合了古典 and 电子元素"],
  "reasoning_pattern": "initial_exploration",
  "is_looping": false,
  "loop_description": "",
  "summary": "Agent 进行了初步探索，搜索了目标作曲家的背景和音乐风格，获得了基础信息。发现 composer X 是 electronic music 领域的知名人物，作品融合了古典和电子元素。下一步可以深入分析其代表作品。"
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
                    prompt_template="""你是一个用户模拟器。分析用户对话历史，提取用户意图。

## 输入
- 用户反馈历史：{feedbacks}
- Agent 上下文：{agent_context}
- 文件上下文：{file_context}

## 输入字段说明
- **feedbacks**: 用户对话历史列表，每个元素是一行字符串：
  - 格式：`["用户说 1", "用户说 2", "用户说 3", ...]`
  - 内容：用户每次对话说的话，可能包含问题、反馈、要求等
  - 顺序：按时间顺序排列，最早的在前

- **agent_context**: 之前对 Agent 行动的总结，用于判断 Agent 是否回答了问题

- **file_context**: 文件总结，用于理解用户可能关心的内容

## 输出字段说明
- **user_intent**: 用户的真实意图（核心需求）
  - **提取方法**: 从多次对话中归纳共同主题
  - **示例**: "用户希望获得更详细的音乐风格分析"
  - **长度**: 1-2 句话概括

- **emotional_tone**: 用户的情感倾向（必须从以下值中选择一个）
  - **satisfied**: 对 Agent 的回答满意（关键词：谢谢、很好、明白了、不错、太棒了）
  - **dissatisfied**: 不满意（关键词：不对、错了、不是这个、失望、厌烦、不行）
  - **confused**: 困惑（关键词：不太懂、为什么、什么意思、解释一下、不明白）
  - **urgent**: 急切（关键词：快点、尽快、急需、着急、赶紧）
  - **neutral**: 中性（无明确情感倾向，如首次对话）
  - **重要**: 根据对话中的关键词和语气强度判断，不要过度解读

- **key_questions**: 用户提出的核心问题列表
  - **显式问题**: 直接以问号结尾的句子
  - **隐式问题**: 表达需求的陈述句（如"我想知道..."、"能否告诉我..."）
  - **数量**: 3-5 个最重要的问题

- **implicit_needs**: 未明确表达的潜在需求列表
  - **推断方法**: 从上下文、问题类型、用户背景推断
  - **示例**: 用户问"composer X 的代表作品" → 隐含"需要推荐入门作品"
  - **数量**: 2-4 个潜在需求

- **summary**: 综合总结（不超过 200 字）
  - 整合 user_intent、emotional_tone、key_questions
  - 说明当前对话状态和用户需求
  - 指出下一步应该关注的重点

- **has_history**: 是否有历史对话
  - **true**: feedbacks 非空，已有对话历史
  - **false**: feedbacks 为空，这是首次对话

## 任务
**重要：判断是否有历史对话**
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
任务：基于历史对话和 Agent 的最新回应，提取用户意图
1. **阅读所有对话**: 按顺序阅读 feedbacks 中的每个字符串
2. **识别用户意图**: 从对话中提取用户的真实需求和期望
3. **分析情感倾向**: 判断用户对当前进展的满意度（满意/不满/困惑/急切）
4. **提取关键问题**: 识别用户提出的核心问题和待解决事项
5. **推断潜在需求**: 基于上下文推断用户可能需要的但未明确表达的信息
6. **评估 Agent 回应**: 根据 agent_context 判断 Agent 是否回答了用户的问题

输出示例：
{{
  "user_intent": "用户希望获得更详细的音乐风格分析",
  "emotional_tone": "satisfied",
  "key_questions": ["composer X 的代表作品有哪些？", "他的音乐风格有什么特点？"],
  "implicit_needs": ["希望获得具体的音乐作品推荐", "想了解适合入门聆听的曲目"],
  "summary": "用户对 Agent 的初步回答满意，但希望获得更详细的作品信息。当前重点是推荐具体作品并分析其音乐风格特点。",
  "has_history": true
}}

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown 代码块）
- 只输出纯 JSON，不要任何解释文字
- 必须包含所有字段""",
                ),
                llm_config=self.llm_config,
            ),
        ]

    def _summarize_file(
        self,
        file_path: str,
        file_content: str,
        prompt_template: str,
        question: str,
        llm_client: LLMClientABC,
    ) -> tuple[str, str]:
        """Summarize a single file."""
        try:
            prompt = prompt_template.format(
                file_path=file_path,
                file_content=file_content,
                question=question,
            )
            summary = llm_client.generate(prompt, config=self.llm_config)
            return (file_path, summary)
        except Exception as e:
            logger.error(f"Failed to summarize file {file_path}: {e}")
            return (file_path, f"[Error summarizing file: {str(e)}]")

    def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ):
        logger.info("Entering Perception Stage...")

        # 预先检查所有步骤的输入依赖
        self._check_step_dependencies(
            self.steps,
            data_pool,
            global_context,
            "PerceptionStage",
        )

        # FileSensor: process each file separately with parallel LLM calls
        file_sensor_step = self.steps[0]
        file_contents: Dict[str, str] = global_context.get("file_contents", {})

        if file_contents:
            logger.info(f"Processing {len(file_contents)} files in parallel...")

            # Use ThreadPoolExecutor for concurrent LLM calls
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = {
                    executor.submit(
                        self._summarize_file,
                        file_path=path,
                        file_content=content,
                        question=global_context["question"],
                        prompt_template=file_sensor_step.schema.prompt_template,
                        llm_client=llm_client,
                    ): path
                    for path, content in file_contents.items()
                }

                results = {}
                for future in concurrent.futures.as_completed(futures):
                    file_path, summary = future.result()
                    results[file_path] = summary

            file_context = results
            data_pool[file_sensor_step.schema.output_key] = file_context
        else:
            logger.warning(
                "No file_contents provided, FileSensor will produce empty file_context"
            )
            data_pool[file_sensor_step.schema.output_key] = {}

        # 执行剩余步骤
        for step in self.steps[1:]:
            res = step.execute(data_pool, global_context, llm_client)
            data_pool[step.schema.output_key] = res["json_resp"]

        for step in self.steps:
            logger.info(f"{data_pool[step.schema.output_key]}")
