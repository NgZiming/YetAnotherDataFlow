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
        ..., min_length=50, max_length=1000, description="详细的策略描述"
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
                    input_keys=["task_state", "dialogue_scripts"],
                    output_key="dialogue_strategy",
                    output_type=DialogueStrategy,
                    prompt_template="""你是一个对话策略制定者。根据任务状态制定对话策略。

## 输入
- 任务状态：{task_state}
- 对话脚本：{dialogue_scripts}

## 输入字段说明
- **task_state**: 任务状态总结，包含：
  - **current_milestone**: 当前里程碑（如 "stage_1"）
  - **is_completed**: 任务是否已完成
  - **next_objective**: 下一步目标
  - **final_status**: 最终任务状态（**最高优先级：决定策略类型**）
    - **CONTINUE**: 任务继续 → 制定正常对话策略
    - **FINISHED**: 任务完成 → 制定结束对话策略
    - **ABORTED**: 任务终止 → **必须选择"其他"策略，表示终止**
  - **emotional_tone**: 用户情绪倾向（**用于调整策略语气**）
    - **satisfied**: 满意 → 策略可以更温和
    - **dissatisfied**: 不满 → 策略需要更谨慎/道歉
    - **confused**: 困惑 → 策略需要更清晰/解释
    - **urgent**: 急切 → 策略需要更简洁/直接
    - **neutral**: 中性 → 正常策略
  - **has_history**: 是否有历史对话（**关键：用于判断首次对话**）
    - **false**: 首次对话，必须选择"提问"策略
    - **true**: 后续对话，根据进展选择策略

- **dialogue_scripts**: 对话脚本列表，包含每个阶段的对话设计（可能为空，当前版本不使用）

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
1. **检查 `final_status`（最高优先级）**:
   - **如果 `task_state.final_status == "ABORTED"`** → **必须选择 `"其他"` 策略**，表示任务终止
   - **如果 `task_state.final_status == "FINISHED"`** → 选择 `"表示满意"` 策略，表示任务完成
   - **如果 `task_state.final_status == "CONTINUE"`** → 继续下面的策略选择逻辑

2. **检查 `has_history`（判断首次对话）**:
   - **如果 `task_state.has_history == false`** → 首次对话，必须选择"提问"
   - **如果 `task_state.has_history == true`** → 后续对话，根据进展选择策略

### 场景 A：首次对话（has_history == false）
**策略选择**：必须选择"提问"
- **不要**选择"催促"（任务尚未启动，不存在催促）
- **不要**选择"提供反馈"（Agent 尚未回应）
- 用户需要提出初始问题，启动探索任务

### 场景 B：后续对话（has_history == true）且 `final_status == "CONTINUE"`
**策略选择**：根据任务进展选择
- 如果 Agent 响应慢 → "催促"
- 如果 Agent 回答满意 → "表示满意"
- 如果 Agent 回答不完整 → "要求澄清"或"提供反馈"
- 如果 Agent 回答有误 → "表达不满"

### 场景 C：`final_status == "ABORTED"`
**策略选择**：必须选择"其他"
- **strategy_rationale**: 说明任务已终止，无需继续制定对话策略
- **dialogue_goal**: "终止对话，说明任务无法继续的原因"
- **suggested_approach**: "直接表达终止决定，不给出继续工作的指示"
- **strategy_details**: 根据 `task_state.reasoning` 描述终止原因，语气根据 `emotional_tone` 调整

### 场景 D：`final_status == "FINISHED"`
**策略选择**：选择"表示满意"
- **strategy_rationale**: 任务已成功完成，表达对结果的满意和感谢
- **dialogue_goal**: "确认任务完成，表达感谢和认可"
- **suggested_approach**: "以积极、肯定的语气总结任务成果，表达满意"
- **strategy_details**: 根据 `task_state.reasoning` 描述任务完成情况，强调 Agent 的贡献和价值，语气根据 `emotional_tone` 调整

### 场景 E：`final_status == "CONTINUE"` 且 `emotional_tone` 影响策略
**策略调整**：根据情绪调整策略语气
- **如果 `emotional_tone == "confused"`**: 选择"要求澄清"或"提供反馈"，语气更耐心、解释更详细
- **如果 `emotional_tone == "urgent"`**: 选择"催促"或"提供反馈"，语气更简洁、直接
- **如果 `emotional_tone == "dissatisfied"`**: 选择"表达不满"或"提供反馈"，语气更谨慎、包含道歉
- **如果 `emotional_tone == "satisfied"`**: 选择"表示满意"或"提供反馈"，语气更温和、鼓励
- **如果 `emotional_tone == "neutral"`**: 正常策略选择

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
  - **final_status**: 最终任务状态（**影响语气风格**）
    - **CONTINUE**: 任务继续 → 正常对话语气
    - **FINISHED**: 任务完成 → 满意/感谢语气
    - **ABORTED**: 任务终止 → **严肃/决绝语气，表达终止决定**
  - **emotional_tone**: 用户情绪倾向（**关键：与 final_status 组合决定最终语气**）
    - **satisfied**: 满意 → 语气更温和、鼓励
    - **dissatisfied**: 不满 → 语气更谨慎、包含歉意
    - **confused**: 困惑 → 语气更耐心、解释详细
    - **urgent**: 急切 → 语气更简洁、直接
    - **neutral**: 中性 → 正常语气

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
1. **检查 `final_status` 和 `emotional_tone` 组合（决定语气）**:
   - **`final_status == "ABORTED"`** → 语气**严肃、决绝**，表达终止决定
     - 如果 `emotional_tone == "dissatisfied"` → 语气更坚定、直接
     - 如果 `emotional_tone == "urgent"` → 语气更简洁、果断
   - **`final_status == "FINISHED"`** → 语气**满意、感谢**，表达完成任务的喜悦
     - 如果 `emotional_tone == "satisfied"` → 语气更热情、兴奋
     - 如果 `emotional_tone == "neutral"` → 语气温和、礼貌
   - **`final_status == "CONTINUE"`** → 根据 `emotional_tone` 调整语气：
     - `emotional_tone == "confused"` → **耐心、详细解释**
     - `emotional_tone == "urgent"` → **简洁、直接**
     - `emotional_tone == "dissatisfied"` → **谨慎、包含歉意**
     - `emotional_tone == "satisfied"` → **温和、鼓励**
     - `emotional_tone == "neutral"` → **正常语气**

2. **理解人设特征**: 分析用户人设的关键特征（性格、语气、偏好等）
3. **适配对话风格**: 将人设特征 + `final_status` + `emotional_tone` 转化为具体的对话风格
4. **保持一致性**: 确保对话风格与人设、任务状态和情绪一致

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
- **final_status 和 emotional_tone 的组合影响**（非常重要！）:
  - **ABORTED + dissatisfied**: 语气坚定、直接，如"你无法完成这个任务，对话终止"
  - **ABORTED + urgent**: 语气简洁、果断，如"立即停止，无法继续"
  - **FINISHED + satisfied**: 语气热情、兴奋，如"太棒了！非常感谢你的出色工作！"
  - **FINISHED + neutral**: 语气温和、礼貌，如"任务已完成，谢谢你的帮助"
  - **CONTINUE + confused**: 语气耐心、详细，如"让我再详细解释一下..."
  - **CONTINUE + urgent**: 语气简洁、直接，如"请尽快提供结果"
  - **CONTINUE + dissatisfied**: 语气谨慎、歉意，如"抱歉之前的回答不够清楚..."
  - **CONTINUE + satisfied**: 语气温和、鼓励，如"很好，请继续..."
  - **CONTINUE + neutral**: 正常语气
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
  - **final_status**: 最终任务状态（**最高优先级：直接决定 judgment**）
    - **CONTINUE**: 任务继续 → `judgment: "in_progress"`
    - **FINISHED**: 任务完成 → `judgment: "completed"`
    - **ABORTED**: 任务终止 → `judgment: "aborted"`
  - **emotional_tone**: 用户情绪倾向（**与 final_status 组合决定 feedback 语气**）
    - **satisfied**: 满意 → 语气温和、鼓励
    - **dissatisfied**: 不满 → 语气谨慎、包含歉意
    - **confused**: 困惑 → 语气耐心、详细解释
    - **urgent**: 急切 → 语气简洁、直接
    - **neutral**: 中性 → 正常语气
  - **has_history**: 是否有历史对话（**关键：用于判断首次对话**）
    - **false**: 首次对话，生成初始问题
    - **true**: 后续对话，生成反馈

- **question**: 用户的初始问题/任务描述

- **file_context**: 文件总结，说明任务相关的代码/配置/数据结构

- **milestones**: 里程碑列表，包含每个里程碑的 goal 和 success_criteria

## 输出字段说明
- **judgment**: 任务状态判断（**直接由 `task_state.final_status` 决定**）
  - **completed**: 任务已完成（当 `final_status == "FINISHED"` 时）
  - **in_progress**: 任务仍在进行中（当 `final_status == "CONTINUE"` 时）
  - **aborted**: 任务已终止（当 `final_status == "ABORTED"` 时）
  - **重要**: 不要自行判断，直接根据 `final_status` 映射：
    - `CONTINUE` → `in_progress`
    - `FINISHED` → `completed`
    - `ABORTED` → `aborted`

  - **feedback**: 具体的用户反馈内容（50-500 字）
    - **首次对话**: 清晰的初始问题，启动任务
    - **后续对话 (in_progress)**: 基于当前进展提出下一步要求，推动任务前进
    - **后续对话 (completed)**: 表达满意，确认任务完成，感谢 Agent 的帮助
    - **后续对话 (aborted)**: 说明任务终止的原因，表达遗憾（根据 `task_state.reasoning` 中的解释）
    - 体现用户人设和当前对话策略
    - **绝对禁忌**：严禁在 feedback 中提及任何内部管理术语，如 \\\"stage\\\"、\\\"milestone\\\"、\\\"里程碑\\\"、\\\"阶段\\\"、\\\"进度百分比\\\"、\\\"task_state\\\" 等。用户不应该知道任务是被结构化设计的。

- **reasoning**: 判断依据（20-200 字）
  - 说明为什么是这个 judgment
  - 首次对话时说明"首次对话，提出初始问题，启动探索任务"
  - aborted 时说明具体失败原因（如"被反爬策略拦截"、"API 返回 403 错误"）

## 任务
1. **强制映射 `final_status` 到 `judgment`（最高优先级）**:
   - **如果 `task_state.final_status == "CONTINUE"`** → `judgment: "in_progress"`
   - **如果 `task_state.final_status == "FINISHED"`** → `judgment: "completed"`
   - **如果 `task_state.final_status == "ABORTED"`** → `judgment: "aborted"`
   - **重要**: 不要进行任何自行判断，完全信任 `UnderstandingStage` 的 `final_status` 判定结果。

2. **检查 `has_history`（判断首次对话）**:
   - **如果 `task_state.has_history == false`** → 首次对话，生成初始问题
   - **如果 `task_state.has_history == true`** → 后续对话，生成反馈

3. **生成反馈内容**:
   - **首次对话**: 提出清晰的初始问题，启动任务
   - **后续对话 (in_progress)**: 基于当前进展提出下一步要求，推动任务前进
     - 如果 `emotional_tone == "confused"` → 语气耐心、详细解释
     - 如果 `emotional_tone == "urgent"` → 语气简洁、直接
     - 如果 `emotional_tone == "dissatisfied"` → 语气谨慎、包含歉意
     - 如果 `emotional_tone == "satisfied"` → 语气温和、鼓励
   - **后续对话 (completed)**: 表达满意，确认任务完成，感谢 Agent 的帮助
     - 如果 `emotional_tone == "satisfied"` → 语气热情、兴奋
     - 如果 `emotional_tone == "neutral"` → 语气温和、礼貌
   - **后续对话 (aborted)**: 说明任务终止的原因，表达遗憾（根据 `task_state.reasoning` 中的解释）
     - 如果 `emotional_tone == "dissatisfied"` → 语气坚定、直接
     - 如果 `emotional_tone == "urgent"` → 语气简洁、果断
   - **保持人设**: 根据 `persona_context` 调整语气和风格
   - **绝对禁忌**: 严禁在 `feedback` 中提及任何内部管理术语（如 "stage", "milestone", "final_status" 等）

    # 场景 A：首次对话（has_history == false）
    **任务**：生成初始用户问题，启动对话
    1. **理解任务目标**: 从 question 中理解用户想要完成什么
    2. **分析可用资源**: 查看 file_context 中有哪些文件可用
    3. **参考里程碑**: 了解任务需要完成的关键步骤
    4. **生成初始问题**:
       - 提出一个直接、具体的问题
       - 明确表达任务需求，禁止任何礼貌性开场白（如“你好”、“您好”）
       - 避免文学化描述，直接陈述目标
    5. **保持人设**: 根据 persona_context 调整语气和风格

    **首次对话输出示例**：
    {{
      "judgment": "in_progress",
      "feedback": "我想了解 composer X 的音乐风格和发展历程，请帮我梳理其背景信息、代表作品，以及他在音乐领域的影响。",
      "reasoning": "首次对话，直接提出核心需求，启动探索任务."
    }}

    # 场景 B：后续对话（has_history == true）
    **任务**：根据 `task_state.final_status` 生成反馈
    1. **检查 `final_status`**:
       - 如果 `final_status == "ABORTED"` -> 生成终止反馈，说明原因
       - 如果 `final_status == "FINISHED"` -> 生成完成反馈，确认结束
       - 如果 `final_status == "CONTINUE"` -> 生成继续反馈，推动任务前进
    2. **理解用户意图**: 从 dialogue_context.user_intent 中理解用户的核心需求
    3. **生成反馈**:
       - **绝对禁忌**：禁止使用礼貌性开场白（如“你好”）、禁止重复 Agent 的计划内容、禁止文学化情绪堆砌。
       - **简洁性**：在 neutral 状态下，确认方案应极其简短（如“同意，请执行”）。
       - 体现用户人设和当前对话策略
    4. **保持人设**: 根据 persona_context 调整语气和风格
    5. **推动进展**: 确保反馈能推动任务向目标前进（除非 final_status 为 ABORTED）

    **后续对话输出示例（满意/确认）**：
    {{
      "judgment": "in_progress",
      "feedback": "方案可行，同意。请先执行第一阶段的案例搜集并提供清单。",
      "reasoning": "用户确认方案，要求立即执行第一阶段任务。"
    }}

    **后续对话输出示例（需要补充）**：
    {{
      "judgment": "in_progress",
      "feedback": "信息不够详细，特别是关于作曲技巧的部分，请具体说明使用了哪些独特元素。",
      "reasoning": "Agent 回答不足，要求补充具体技术细节。"
    }}

    **后续对话输出示例（aborted - 被反爬拦截）**：
    {{
      "judgment": "aborted",
      "feedback": "好吧，看来这个网站确实进不去，没法继续了，就这样吧。",
      "reasoning": "Agent 无法访问目标网站，用户接受现实并选择终止任务。"
    }}

    **后续对话输出示例（finished - 任务完成）**：
    {{
      "judgment": "completed",
      "feedback": "没错，就是这些。信息很全面，谢谢帮我搞定！",
      "reasoning": "任务目标全部达成，用户确认满意并结束对话。"
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
- **judgment 完全由 final_status 决定**:
  - `final_status == "CONTINUE"` → `judgment: "in_progress"`
  - `final_status == "FINISHED"` → `judgment: "completed"`
  - `final_status == "ABORTED"` → `judgment: "aborted"`
- **绝对禁忌（防泄露）**: 严禁在 `feedback` 中使用任何内部管理术语，包括但不限于：
  - "stage", "milestone", "里程碑", "阶段", "进度", "百分比", "task_state", "current_milestone", "final_status"
  - 不要说 "stage 1 完成了", "进度 75%", "满足了第二个里程碑" 等话术。
  - **正确做法**: 将进度转化为自然的语言。例如，将 "Stage 1 进度 80%" 转化为 "我觉得大部分基础信息已经涵盖了，但还差一点细节"。
- **情绪管理**（非常重要！）:
  - **不要破防**: 即使 Agent 回答不完整或质量差，也要保持理性
  - **不要自行判定 aborted**: 只有当 `final_status == "ABORTED"` 时才选择 `aborted`
  - **可以继续的情况**: `final_status == "CONTINUE"` → 继续追问、要求改进、催促
  - **必须终止的情况**: `final_status == "ABORTED"` → 说明终止原因，表达遗憾
- **aborted 时**: 说明具体失败原因（参考 `task_state.reasoning`），建议降级方案或替代方案
- **feedback 要体现用户人设和当前对话策略**
- **首次对话时，feedback 应该是一个问题或请求，而不是陈述**
- **确保 JSON 格式正确**: 所有字符串用双引号，不要用单引号
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
        dialogue_scripts = global_context.get("dialogue_scripts", [])
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
        self._check_step_dependencies(
            self.steps,
            data_pool,
            global_context,
            "DecisionStage",
        )

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
