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

## 重要：判断是否为首次对话
**首次对话检测**：
- 如果 `dialogue_context.has_history == false`：这是首次对话，需要用户主动提问启动任务
- 如果 `dialogue_context.has_history == true`：这是后续对话，根据进展选择策略

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

## 任务
1. **判断是否首次对话**: 检查 dialogue_context.has_history
2. **分析任务状态**: 理解当前任务的进展和需求
3. **选择对话策略**: 
   - 首次对话 → "提问"
   - 后续对话 → 根据进展选择（催促/满意/澄清/不满）
4. **制定下一步**: 确定下一步对话的目标和方式

## 输出要求（非常重要！）
- **只输出纯 JSON，不要 Markdown 代码块（不要 ```json ... ```）**
- **不要输出任何解释文字、前言、后语**
- **必须严格包含以下字段，不能缺少**
- 输出示例（直接复制这个格式，替换值）：
{{
  "strategy_type": "提问",
  "strategy_rationale": "这是首次对话，用户需要提出初始问题来启动探索任务。",
  "dialogue_goal": "获取关于作曲家 X 的基本信息和音乐风格概述",
  "suggested_approach": "以开放式问题开始，邀请 Agent 分享相关知识",
  "strategy_details": "用户希望了解作曲家 X 的背景、代表作品和音乐风格特点。这是一个探索性任务，需要通过提问引导 Agent 提供全面的信息。首次对话应该以友好、开放的方式开始，表达对主题的興趣，并明确希望了解的具体方面。"
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
  "persona_context": "用户是一位关注社会正义的普通市民，语气专业但不失温和，喜欢用具体案例来理解问题，对法律细节不太熟悉但希望获得清晰易懂的解释。"
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

## 重要：判断是否为首次对话
**首次对话检测**：
- 如果 dialogue_context.has_history == false：这是首次对话
- 如果 dialogue_context.has_history == true：这是后续对话

### 场景 A：首次对话（has_history == false）
任务：生成初始用户问题，启动对话
1. **理解任务目标**: 从 question 中理解用户想要完成什么
2. **分析可用资源**: 查看 file_context 中有哪些文件可用
3. **参考里程碑**: 了解任务需要完成的关键步骤
4. **生成初始问题**: 
   - 提出一个清晰、具体的问题
   - 表达对任务的期望
   - 可以提及关心的具体方面
5. **保持人设**: 根据 persona_context 调整语气和风格

首次对话输出示例：
{{
  "judgment": "in_progress",
  "feedback": "你好！我想了解 composer X 的音乐风格和发展历程。我注意到有一些相关的文档和代码文件，希望你能帮我梳理一下这位作曲家的背景信息、代表作品，以及他在音乐领域的影响。特别是想了解他的创作风格有什么特点，以及他的作品对后来的音乐家有什么影响。",
  "reasoning": "首次对话，提出初始问题，启动探索任务。"
}}

### 场景 B：后续对话（has_history == true）
任务：基于历史对话和 Agent 的回应，生成新的用户反馈
1. **理解用户意图**: 从 dialogue_context.user_intent 中理解用户的核心需求
2. **评估 Agent 回应**: 根据 task_state 判断 Agent 是否回答了用户的问题
3. **生成反馈**: 
   - 如果 Agent 回答满意：表达感谢，提出更深入的问题
   - 如果 Agent 回答不完整：指出缺失的部分，请求补充
   - 如果 Agent 回答有误：礼貌地纠正，提供正确方向
4. **保持人设**: 根据 persona_context 调整语气和风格
5. **推动进展**: 确保反馈能推动任务向里程碑目标前进

后续对话输出示例（满意）：
{{
  "judgment": "in_progress",
  "feedback": "谢谢你的详细解答！关于 composer X 的背景信息很清楚了。不过我还想了解一些更具体的内容：他的代表作品中，哪几首最能体现他的音乐风格？能否推荐一些适合入门聆听的作品？",
  "reasoning": "用户对初步回答满意，希望获得更具体的作品推荐。"
}}

后续对话输出示例（需要补充）：
{{
  "judgment": "in_progress",
  "feedback": "你提到的这些信息很有帮助，但我感觉还缺少一些关键内容。特别是关于 composer X 的音乐风格分析不够详细，能否具体说明他的作品中使用了哪些独特的作曲技巧或音乐元素？",
  "reasoning": "Agent 的回答不够详细，需要补充更具体的风格分析。"
}}

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown）
- 必须包含以下字段：
  - judgment: completed|in_progress|aborted
  - feedback: 具体的用户反馈内容，100-300 字
  - reasoning: 判断依据，50-100 字

## 重要提示
- **首次对话**: feedback 应该是清晰的初始问题，启动任务
- **后续对话**: feedback 应该基于历史对话，推动任务进展
- judgment 为 "completed" 时：任务已满足所有要求，所有里程碑完成
- judgment 为 "in_progress" 时：任务仍在进行中，需要继续对话
- judgment 为 "aborted" 时：任务无法继续或已放弃
- feedback 要体现用户人设和当前对话策略
- 首次对话时，feedback 应该是一个问题或请求，而不是陈述""",
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
