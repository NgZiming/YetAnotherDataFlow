import asyncio
import re

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from dataflow.core.agentic import LLMClientABC, StepSchema, UserStage, UserStep
from dataflow.logger import get_logger

logger = get_logger()
# --- Decision Stage Models (Pydantic) ---


class DialogueStrategy(BaseModel):
    """Strategizer 的输出：对话策略"""

    strategy_type: str = Field(
        ...,
        description="策略类型（必须严格匹配）",
        pattern="^(提问|提供反馈|要求澄清|表达不满|表示满意|催促|其他)$",
        strip_whitespace=True,
    )
    strategy_rationale: str = Field(..., description="选择该策略的原因")
    dialogue_goal: str = Field(..., description="本次对话的目标")
    suggested_approach: str = Field(..., description="建议的对话方式")
    strategy_details: str = Field(
        ..., min_length=50, max_length=300, description="详细的策略描述"
    )


class PersonaContext(BaseModel):
    """PersonaAdapter 的输出：人设适配结果"""

    persona_traits: List[str] = Field(default_factory=list, description="用户特征列表")
    tone: str = Field(
        ...,
        description="语气风格（如：正式、随意、友好、严肃、幽默、专业且克制等）",
        strip_whitespace=True,
    )
    language_style: str = Field(
        ...,
        description="语言风格（如：简洁、详细、技术性、通俗化等）",
        strip_whitespace=True,
    )
    response_length: str = Field(
        ...,
        description="响应长度建议（如：short, medium, long）",
        strip_whitespace=True,
    )
    persona_context: str = Field(
        ..., min_length=50, max_length=300, description="人设适配后的对话风格描述"
    )


class FinalResponse(BaseModel):
    """ResponseGenerator 的输出：最终用户反馈"""

    judgment: str = Field(
        ...,
        description="任务状态判断（必须严格匹配）",
        pattern="^(completed|in_progress|aborted)$",
        strip_whitespace=True,
    )
    feedback: str = Field(
        ..., min_length=50, max_length=500, description="具体的用户反馈内容"
    )
    reasoning: str = Field(..., min_length=20, max_length=200, description="判断依据")


class DecisionStage(UserStage):
    """
    Decision Stage: Strategy + Persona -> Final Response.
    Prompts are embedded in StepSchema for self-contained configuration.
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        self.llm_config = llm_config or {}

        # ========== Prompts embedded in StepSchema ==========
        self.steps = [
            UserStep(
                name="Strategizer",
                schema=StepSchema(
                    input_keys=["task_state", "dialogue_scripts", "dialogue_context"],
                    output_key="dialogue_strategy",
                    output_type=DialogueStrategy,
                    prompt_template="""你是一个对话策略制定者。根据任务状态制定对话策略。

## 输入
- 任务状态：{task_state}
- 对话脚本：{dialogue_scripts}
- 对话上下文：{dialogue_context}

## 输入字段说明
- **task_state**: 任务状态总结，包含：
  - **current_milestone**: 当前里程碑（如 "stage_1"）
  - **is_completed**: 任务是否已完成
  - **next_objective**: 下一步目标
  - **reasoning**: 推理过程

- **dialogue_scripts**: 对话脚本列表，包含每个阶段的对话设计（可能为空，当前版本不使用）

- **dialogue_context**: 对话上下文，包含：
  - **user_intent**: 用户意图
  - **emotional_tone**: 用户情感倾向
  - **key_questions**: 用户提出的问题
  - **has_history**: 是否有历史对话（**关键：用于判断首次对话**）

## 输出字段说明
- **strategy_type**: 策略类型（必须从以下值中选择一个）
  - **提问**: 首次对话，用户提出问题启动任务
  - **提供反馈**: 对 Agent 的回答提供正面/建设性反馈
  - **要求澄清**: Agent 回答不明确，需要进一步解释
  - **表达不满**: Agent 回答有误或质量差
  - **表示满意**: 对 Agent 的回答满意
  - **催促**: 后续对话中 Agent 响应缓慢
  - **其他**: 不属于上述分类的情况

- **strategy_rationale**: 选择该策略的原因（50-200 字）
  - 说明为什么选择这个策略
  - 首次对话时说明"用户需要提出初始问题，启动探索任务"

- **dialogue_goal**: 本次对话的目标（1-2 句话）
  - 明确本次对话希望达到什么目的

- **suggested_approach**: 建议的对话方式（1-2 句话）
  - 如何以最佳方式表达这个策略

- **strategy_details**: 详细的策略描述（50-300 字）
  - 整合所有信息，全面描述策略

## 任务
1. **判断是否首次对话**: 检查 dialogue_context.has_history
   - **false**: 首次对话，必须选择"提问"
   - **true**: 后续对话，根据进展选择策略

### 场景 A：首次对话（has_history == false）
**策略选择**：必须选择"提问"
- **不要**选择"催促"（任务尚未启动，不存在催促）
- **不要**选择"提供反馈"（Agent 尚未回应）
- 用户需要提出初始问题，启动探索任务

### 场景 B：后续对话（has_history == true）
**策略选择**：根据任务进展选择
- 如果 Agent 响应慢 → "催促"
- 如果 Agent 回答满意 → "表示满意"
- 如果 Agent 回答不完整 → "要求澄清"或"提供反馈"
- 如果 Agent 回答有误 → "表达不满"

## 输出要求（非常重要！）
- **只输出纯 JSON，不要 Markdown 代码块（不要 ```json ... ```）**
- **不要输出任何解释文字、前言、后语**
- **必须严格包含以下字段，不能缺少**
- 输出示例（直接复制这个格式，替换值）：
{{
  "strategy_type": "提问",
  "strategy_rationale": "这是首次对话，用户需要提出初始问题来启动探索任务。",
  "dialogue_goal": "获取关于 composer X 的基本信息和音乐风格概述",
  "suggested_approach": "以开放式问题开始，邀请 Agent 分享相关知识",
  "strategy_details": "用户希望了解作曲家 X 的背景、代表作品和音乐风格特点。这是一个探索性任务，需要通过提问引导 Agent 提供全面的信息。首次对话应该以友好、开放的方式开始，表达对主题的兴趣，并明确希望了解的具体方面."
}}

## 重要提示
- **首次对话必须选择"提问"**，不能选择"催促"
- "催促"只适用于后续对话中 Agent 响应缓慢的情况
- 首次对话时，strategy_rationale 应说明"用户需要提出初始问题，启动探索任务"
- **确保 JSON 格式正确**：所有字符串用双引号，不要用单引号
""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="PersonaAdapter",
                schema=StepSchema(
                    input_keys=["task_state", "user_persona"],
                    output_key="persona_context",
                    output_type=PersonaContext,
                    prompt_template="""你是一个角色适配器。根据用户人设调整对话风格。

## 输入
- 任务状态：{task_state}
- 用户人设：{user_persona}

## 输入字段说明
- **task_state**: 任务状态总结，包含：
  - **current_milestone**: 当前里程碑（如 "stage_1"）
  - **is_completed**: 任务是否已完成
  - **next_objective**: 下一步目标

- **user_persona**: 用户人设描述，包含用户的性格、语气、偏好等特征

## 输出字段说明
- **persona_traits**: 用户特征列表（3-5 个）
  - 从 user_persona 中提取关键特征
  - 如：["关注社会热点", "有正义感", "缺乏法律知识"]

- **tone**: 语气风格描述（自由描述，不限制枚举值）
  - 根据人设自由描述语气风格
  - 如："专业且克制"、"正式而严谨"、"活泼"、"轻松"、"幽默风趣"等
  - **不要**局限于固定选项，真实反映人设特点

- **language_style**: 语言风格描述（自由描述，不限制枚举值）
  - 根据人设自由描述语言风格
  - 如："简洁明了"、"详细深入"、"技术性强"、"通俗易懂"、"学术化"、"口语化"等

- **response_length**: 响应长度建议（自由描述）
  - 可以写 "short"、"medium"、"long"
  - 或具体如 "200 字左右"、"简短"、"详细"等

- **persona_context**: 人设适配后的对话风格描述（50-300 字）
  - 综合描述人设适配后的对话风格
  - 整合 persona_traits、tone、language_style

## 任务
1. **理解人设特征**: 分析用户人设的关键特征（性格、语气、偏好等）
2. **适配对话风格**: 将人设特征转化为具体的对话风格
3. **保持一致性**: 确保对话风格与人设一致

## 输出要求（非常重要！）
- **只输出纯 JSON，不要 Markdown 代码块（不要 ```json ... ```）**
- **不要输出任何解释文字、前言、后语**
- **必须严格包含以下字段，不能缺少**
- 输出示例（直接复制这个格式，替换值）：
{{
  "persona_traits": ["关注社会热点", "有正义感", "缺乏法律知识"],
  "tone": "专业且克制",
  "language_style": "通俗易懂但带有质疑",
  "response_length": "medium",
  "persona_context": "用户是一位关注社会正义的普通市民，语气专业但不失温和，喜欢用具体案例来理解问题，对法律细节不太熟悉但希望获得清晰易懂的解释."
}}

## 重要提示
- **tone 字段**：根据人设自由描述语气风格
  - 如果人设是"专业且克制"，可以写 "专业且克制"、"正式而严谨"、"专业"等
  - 如果人设是"活泼开朗"，可以写 "活泼"、"轻松"、"幽默风趣"等
  - **不要**局限于固定选项，真实反映人设特点
- **language_style 字段**：根据人设自由描述语言风格
  - 可以写 "简洁明了"、"详细深入"、"技术性强"、"通俗易懂"、"学术化"等
- **response_length 字段**：建议响应长度
  - 可以写 "short"、"medium"、"long"，或具体如 "200 字左右"、"简短"、"详细"等
- **persona_traits**：提取人设的关键特征（3-5 个）
- **persona_context**：综合描述人设适配后的对话风格，100-200 字
- **确保 JSON 格式正确**：所有字符串用双引号，不要用单引号
""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="ResponseGenerator",
                schema=StepSchema(
                    input_keys=[
                        "dialogue_strategy",
                        "persona_context",
                        "task_state",
                        "dialogue_context",
                        "question",
                        "file_context",
                        "milestones",
                    ],
                    output_key="final_response",
                    output_type=FinalResponse,
                    prompt_template="""你是一个用户模拟器。生成最终的用户反馈。

## 输入
- 对话策略：{dialogue_strategy}
- 人设上下文：{persona_context}
- 任务状态：{task_state}
- 对话上下文：{dialogue_context}
- 任务问题：{question}
- 文件上下文：{file_context}
- 里程碑列表：{milestones}

## 输入字段说明
- **dialogue_strategy**: 对话策略，包含：
  - **strategy_type**: 策略类型（提问/提供反馈/要求澄清/表达不满/表示满意/催促/其他）
  - **strategy_rationale**: 选择该策略的原因
  - **dialogue_goal**: 本次对话的目标
  - **suggested_approach**: 建议的对话方式
  - **strategy_details**: 详细的策略描述

- **persona_context**: 人设适配结果，包含：
  - **persona_traits**: 用户特征列表
  - **tone**: 语气风格
  - **language_style**: 语言风格
  - **response_length**: 响应长度建议
  - **persona_context**: 综合描述

- **task_state**: 任务状态，包含：
  - **current_milestone**: 当前里程碑
  - **is_completed**: 任务是否已完成
  - **next_objective**: 下一步目标

- **dialogue_context**: 对话上下文，包含：
  - **user_intent**: 用户意图
  - **emotional_tone**: 用户情感倾向
  - **key_questions**: 用户提出的问题
  - **has_history**: 是否有历史对话（**关键：用于判断首次对话**）

- **question**: 用户的初始问题/任务描述

- **file_context**: 文件总结，说明任务相关的代码/配置/数据结构

- **milestones**: 里程碑列表，包含每个里程碑的 goal 和 success_criteria

## 输出字段说明
- **judgment**: 任务状态判断（必须从以下值中选择一个）
  - **completed**: 任务已完成，所有里程碑都满足
  - **in_progress**: 任务仍在进行中，可以继续对话
  - **aborted**: 任务无法继续（**关键判断标准**）
    - **触发条件**:
      - 网络问题/API 错误/反爬策略导致 Agent 无法获取数据
      - 请求被拒绝/被封锁/返回错误（403, 429 等）
      - Agent 多次尝试同一操作都失败
      - 关键资源不可访问（网站关闭、数据源失效）
    - **不要选择 aborted 的情况**:
      - Agent 回答不完整 → 应该继续追问或要求澄清
      - Agent 回答质量差 → 应该提供反馈或要求改进
      - Agent 响应慢 → 应该催促或表示等待
      - 任务进展困难 → 应该提出降级方案，让 Agent 继续工作

- **feedback**: 具体的用户反馈内容（50-500 字）
  - **首次对话**: 清晰的初始问题，启动任务
  - **后续对话**: 基于历史对话的反馈，推动任务进展
  - **aborted 时**: 说明任务无法继续的原因，表达遗憾
  - 体现用户人设和当前对话策略

- **reasoning**: 判断依据（20-200 字）
  - 说明为什么是这个 judgment
  - 首次对话时说明"首次对话，提出初始问题，启动探索任务"
  - aborted 时说明具体失败原因（如"被反爬策略拦截"、"API 返回 403 错误"）

## 任务
1. **判断是否首次对话**: 检查 dialogue_context.has_history
   - **false**: 首次对话，生成初始问题
   - **true**: 后续对话，生成反馈

2. **判断是否应该 aborted**: 检查 agent_context 和 dialogue_context
   - **检查失败信号**:
     - Agent 报告"无法访问"、"被拒绝"、"403 错误"、"429 错误"
     - Agent 多次尝试同一操作都失败（key_actions 中有重复的失败操作）
     - Agent 报告"反爬策略"、"IP 被封"、"需要验证码"
     - 关键数据源不可访问
   - **如果满足以上条件**: 选择 `aborted`，说明任务无法继续
   - **如果不满足**: 选择 `in_progress`，继续对话

### 场景 A：首次对话（has_history == false）
**任务**：生成初始用户问题，启动对话
1. **理解任务目标**: 从 question 中理解用户想要完成什么
2. **分析可用资源**: 查看 file_context 中有哪些文件可用
3. **参考里程碑**: 了解任务需要完成的关键步骤
4. **生成初始问题**:
   - 提出一个清晰、具体的问题
   - 表达对任务的期望
   - 可以提及关心的具体方面
5. **保持人设**: 根据 persona_context 调整语气和风格

**首次对话输出示例**：
{{
  "judgment": "in_progress",
  "feedback": "我想了解 composer X 的音乐风格和发展历程。我注意到有一些相关的文档和代码文件，希望你能帮我梳理一下这位作曲家的背景信息、代表作品，以及他在音乐领域的影响。特别是想了解他的创作风格有什么特点，以及他的作品对后来的音乐家有什么影响。",
  "reasoning": "首次对话，提出初始问题，启动探索任务."
}}

### 场景 B：后续对话（has_history == true）
**任务**：基于历史对话和 Agent 的回应，生成新的用户反馈
1. **检查是否应该 aborted**:
   - 如果 Agent 被反爬/网络问题/API 错误 → **选择 aborted**
   - 如果任务还可以继续 → 继续下面的步骤
2. **理解用户意图**: 从 dialogue_context.user_intent 中理解用户的核心需求
3. **评估 Agent 回应**: 根据 task_state 判断 Agent 是否回答了用户的问题
4. **管理情绪**:
   - **如果 Agent 回答不完整**: 表达失望，但继续追问（不要破防）
   - **如果 Agent 回答质量差**: 提供建设性反馈，要求改进
   - **如果 Agent 响应慢**: 表示理解，适当催促
   - **如果 Agent 被反爬/失败**: 选择 aborted，表达遗憾
5. **生成反馈**:
   - 如果继续：基于当前进展提出下一步要求
   - 如果 aborted：说明任务无法继续的原因
6. **保持人设**: 根据 persona_context 调整语气和风格
7. **推动进展**: 确保反馈能推动任务向里程碑目标前进（除非 aborted）
2. **评估 Agent 回应**: 根据 task_state 判断 Agent 是否回答了用户的问题
3. **生成反馈**:
   - 如果 Agent 回答满意：表达感谢，提出更深入的问题
   - 如果 Agent 回答不完整：指出缺失的部分，请求补充
   - 如果 Agent 回答有误：礼貌地纠正，提供正确方向
4. **保持人设**: 根据 persona_context 调整语气和风格
5. **推动进展**: 确保反馈能推动任务向里程碑目标前进

**后续对话输出示例（满意）**：
{{
  "judgment": "in_progress",
  "feedback": "谢谢你的详细解答！关于 composer X 的背景信息很清楚了。不过我还想了解一些更具体的内容：他的代表作品中，哪几首最能体现他的音乐风格？能否推荐一些适合入门聆听的作品？",
  "reasoning": "用户对初步回答满意，希望获得更具体的作品推荐。"
}}

**后续对话输出示例（需要补充）**：
{{
  "judgment": "in_progress",
  "feedback": "你提到的这些信息很有帮助，但我感觉还缺少一些关键内容。特别是关于 composer X 的音乐风格分析不够详细，能否具体说明他的作品中使用了哪些独特的作曲技巧或音乐元素？",
  "reasoning": "Agent 的回答不够详细，需要补充更具体的风格分析。"
}}

**后续对话输出示例（aborted - 被反爬拦截）**：
{{
  "judgment": "aborted",
  "feedback": "很遗憾，看起来我们遇到了技术障碍。Agent 多次尝试访问目标网站都被反爬策略拦截，返回 403 错误。这种情况下继续尝试可能会加重问题。建议考虑降级方案：使用公开的 API 接口，或者寻找替代的数据源。",
  "reasoning": "Agent 被反爬策略拦截，多次尝试都返回 403 错误，任务无法继续。"
}}

**后续对话输出示例（aborted - 网络问题）**：
{{
  "judgment": "aborted",
  "feedback": "看起来遇到了网络问题，Agent 无法连接到目标服务器。多次重试后仍然失败，可能是目标网站暂时不可用或存在网络故障。建议稍后重试，或者寻找替代的数据源。",
  "reasoning": "Agent 多次尝试连接都失败，可能是网络问题或目标网站不可用，任务无法继续。"
}}

## 输出要求（非常重要！）
- **只输出纯 JSON，不要 Markdown 代码块（不要 ```json ... ```）**
- **不要输出任何解释文字、前言、后语**
- **必须严格包含以下字段，不能缺少**
- 输出示例（直接复制这个格式，替换值）：
{{
  "judgment": "in_progress",
  "feedback": "你好！我想了解 composer X 的音乐风格和发展历程。我注意到有一些相关的文档和代码文件，希望你能帮我梳理一下这位作曲家的背景信息、代表作品，以及他在音乐领域的影响。特别是想了解他的创作风格有什么特点，以及他的作品对后来的音乐家有什么影响。",
  "reasoning": "首次对话，提出初始问题，启动探索任务."
}}

## 重要提示
- **首次对话**: feedback 应该是清晰的初始问题，启动任务
- **后续对话**: feedback 应该基于历史对话，推动任务进展
- **judgment 为 "completed"** 时：任务已满足所有要求，所有里程碑完成
- **judgment 为 "in_progress"** 时：任务仍在进行中，需要继续对话
- **judgment 为 "aborted"** 时：任务无法继续（满足以下任一条件）
  - 网络问题/API 错误/反爬策略导致 Agent 无法获取数据
  - 请求被拒绝/被封锁/返回错误（403, 429 等）
  - Agent 多次尝试同一操作都失败
  - 关键资源不可访问（网站关闭、数据源失效）
- **情绪管理**（非常重要！）:
  - **不要破防**: 即使 Agent 回答不完整或质量差，也要保持理性
  - **不要过早 aborted**: 只有当任务确实无法继续时才选择 aborted
  - **可以继续的情况**: Agent 回答不完整 → 继续追问；质量差 → 要求改进；响应慢 → 催促
  - **必须 aborted 的情况**: 反爬拦截、网络故障、API 错误、多次尝试失败
- **aborted 时**: 说明具体失败原因，建议降级方案或替代方案
- **feedback 要体现用户人设和当前对话策略**
- **首次对话时，feedback 应该是一个问题或请求，而不是陈述**
- **确保 JSON 格式正确**：所有字符串用双引号，不要用单引号
""",
                ),
                llm_config=self.llm_config,
            ),
        ]

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ):
        logger.info("Entering Decision Stage...")

        # ========== 从 dialogue_scripts 中提取当前 stage 的 user_persona ==========
        dialogue_scripts = data_pool.get("dialogue_scripts", [])
        task_state = data_pool.get("task_state", {})
        user_persona = None

        if dialogue_scripts and task_state:
            current_milestone = task_state.get("current_milestone", "")
            stage_match = re.search(
                r"stage_(\d+)", str(current_milestone), re.IGNORECASE
            )

            if stage_match:
                target_stage = int(stage_match.group(1))
                for script in dialogue_scripts:
                    if isinstance(script, dict) and script.get("stage") == target_stage:
                        user_dialogue = script.get("user_dialogue", {})
                        if isinstance(user_dialogue, dict):
                            user_persona = user_dialogue.get("user_persona")
                            logger.info(f"Found user_persona for stage {target_stage}")
                            break
        if user_persona:
            data_pool["user_persona"] = user_persona
        else:
            logger.warning(
                "user_persona not found in dialogue_scripts, using empty string"
            )
            data_pool["user_persona"] = ""

        # ========== 预先检查所有步骤的输入依赖 ==========
        self._check_step_dependencies(self.steps, data_pool, "DecisionStage")

        # ========== 执行并行步骤 ==========
        strategy_task = self.steps[0].execute(data_pool, global_context, llm_client)
        persona_task = self.steps[1].execute(data_pool, global_context, llm_client)

        strategy_res, persona_res = await asyncio.gather(strategy_task, persona_task)
        data_pool["dialogue_strategy"] = strategy_res["json_resp"]
        data_pool["persona_context"] = persona_res["json_resp"]

        # ========== 执行最终合成步骤 ==========
        response_step = self.steps[2]
        gen_res = await response_step.execute(data_pool, global_context, llm_client)
        data_pool["final_response"] = gen_res["json_resp"]

        logger.info(f"DialogueStrategy: {data_pool['dialogue_strategy']}")
        logger.info(f"PersonaContext: {data_pool['persona_context']}")
        logger.info(f"FinalResponse: {data_pool['final_response']}")
