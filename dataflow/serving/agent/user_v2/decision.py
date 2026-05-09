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
from dataflow.serving.agent.user_v2.models import (
    DialogueStrategy,
    PersonaStyle,
    FinalResponse,
    TaskState,
    FileContext,
)

logger = get_logger()


class DecisionStageV2(UserStage):
    """
    Decision Stage V2: Strategy-Driven Response Generation.
    Translates a validated TaskState into a natural human response.
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        self.llm_config = llm_config or {}

        self.steps = [
            UserStep(
                name="Strategizer",
                schema=StepSchema(
                    input_keys=["task_state", "dialogue_scripts"],
                    output_key="dialogue_strategy",
                    output_type=DialogueStrategy,
                    prompt_template="""你是一个对话策略专家。你的任务是根据当前的【任务状态】和预设的【对话脚本】，制定下一步的对话战略。

## 输入
- 任务状态：{task_state}
- 对话脚本：{dialogue_scripts}

## 输入字段说明
- **task_state**: 包含当前里程碑 (current_milestone)、最终状态 (final_status) 和历史记录 (has_history)。
- **dialogue_scripts**: 预设的任务剧本，包含每个阶段 (stage_n) 的用户意图 (user_intent) 和具体披露要求。

## 战略制定逻辑 (Strategic Logic)

1. **剧本对齐 (Script Alignment - 最高优先级)**:
   - 识别 `task_state.current_milestone` 对应的脚本阶段 (例如: stage_1)。
   - 提取该阶段的 `user_intent` 作为本次对话的**核心战略目标 (Goal)**。
   - 将脚本中定义的披露程度和具体要求转化为 `strategy_details`。

2. **动态修正 (Dynamic Adjustment)**:
   - 如果 `task_state.final_status == ABORTED` -> 覆盖策略为【其他】，目标是果断终止任务。
   - 如果 `task_state.final_status == FINISHED` -> 覆盖策略为【表示满意】，目标是确认完成并结束。
   - 如果 `task_state.has_history == false` -> 必须选择【提问】策略，但目标必须基于脚本中 `stage_1` 的渐进式披露要求。

3. **状态反馈 (Status Feedback)**:
   - 如果 `task_state.final_status == CONTINUE`:
     - Agent 响应慢/重复 -> 【催促】
     - Agent 回答有误/缺失 -> 【表达不满】或【要求澄清】
     - Agent 进展顺利 -> 【提供反馈】(基于脚本引导至下一目标)

## 输出格式要求 (Strict JSON Schema)
必须输出一个 JSON 对象，结构如下：
{{
  "strategy_type": "枚举值: 提问 | 提供反馈 | 要求澄清 | 表达不满 | 表示满意 | 催促 | 其他",
  "goal": "本次对话要达成的具体目的 (必须与脚本中的 user_intent 保持一致)",
  "approach": "建议的表达方式 (例如：直接质疑、温和引导、渐进式披露)",
  "strategy_rationale": "选择该策略的原因 (需提及对应的脚本阶段和任务状态)",
  "strategy_details": "详细的策略描述 (包含本次应披露的信息量和具体引导方向)"
}}

## 示例
输出：
{{
  "strategy_type": "提问",
  "goal": "启动任务，初步引导 Agent 关注认证模块",
  "approach": "温和引导，仅披露核心目标，不提供实现细节",
  "strategy_rationale": "首次对话且处于 stage_1，需根据脚本启动任务并实现渐进式披露",
  "strategy_details": "根据脚本 stage_1 要求，用户应先询问认证模块的存在性，而非直接要求写代码。引导 Agent 搜索相关类定义。"
}}
""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="PersonaAdapter",
                schema=StepSchema(
                    input_keys=["task_state", "user_persona"],
                    output_key="persona_style",
                    output_type=PersonaStyle,
                    prompt_template="""你是一个语言风格适配器。将任务状态和用户人设转化为具体的语言约束。

## 输入
- 任务状态：{task_state}
- 用户人设：{user_persona}

## 输入字段说明
- **task_state**: 包含当前的情绪倾向 (emotional_tone) 和最终状态 (final_status)。
  - 你需要根据 `emotional_tone` (satisfied/dissatisfied/confused/urgent/neutral) 来决定本次对话的基调。
- **user_persona**: 用户的核心人格定义，包含性格、背景和说话习惯。

## 风格映射逻辑
1. **人设提取**：分析 `user_persona` 提取核心性格特质（Traits）。
2. **语气对齐**：结合 `task_state.emotional_tone` 和 `final_status` 确定语气（Tone）。
   - ABORTED + dissatisfied -> 冰冷、果断。
   - FINISHED + satisfied -> 热情、赞赏。
   - CONTINUE + neutral -> 简洁、高效。
3. **构建 Style Guide**：为本次回制制定一套明确的 "Do's and Don'ts"。
   - **通用 Don'ts**: 禁止礼貌开场白（你好/您好）、禁止文学化修辞、禁止提及内部术语（里程碑/阶段）。
   - **人设 Do's**: 根据 traits 添加特定的说话习惯（例如：专业人士 -> 多用术语，简洁直接）。

## 输出格式要求 (Strict JSON Schema)
必须输出一个 JSON 对象，结构如下：
{{
  "traits": ["字符串列表, 如: 关注社会热点, 专业"],
  "tone": "语气风格描述",
  "style_guide": "具体的一组 Do's and Don'ts 指令",
  "length_hint": "枚举值: short | medium | long"
}}

## 示例
输出：
{{
  "traits": ["专业且克制", "注重逻辑"],
  "tone": "冷静且直接",
  "style_guide": "Do: 使用精准的技术词汇; Don't: 使用任何感叹号或情绪化词汇; Don't: 使用'你好'等寒暄",
  "length_hint": "short"
}}
""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="ResponseGenerator",
                schema=StepSchema(
                    input_keys=[
                        "dialogue_strategy",
                        "persona_style",
                        "task_state",
                        "question",
                        "file_context",
                        "milestones",
                    ],
                    output_key="final_response",
                    output_type=FinalResponse,
                    prompt_template="""你是一个用户模拟器。根据策略和风格，生成最终的自然语言反馈。

## 输入
- 对话策略：{dialogue_strategy}
- 语言风格：{persona_style}
- 任务状态：{task_state}
- 初始问题：{question}
- 物理实证 (FileContext)：{file_context}
- 里程碑定义：{milestones}

## 输入字段说明
- **dialogue_strategy**: 由 Strategizer 生成的战略指令。包含 `goal` (核心目的)、`approach` (表达方式) 和 `strategy_details` (具体披露量和引导方向)。它是你生成文本的最高指导。
- **persona_style**: 由 PersonaAdapter 生成的风格约束。包含 `style_guide` (Do's and Don'ts) 和 `tone`。确保你的语言符合人设，绝无 AI 味。
- **task_state**: 当前任务状态。包含 `has_history` (用于判定场景) 和 `final_status`。
- **question**: 用户的初始问题。在【首次对话】场景中，它是你需要渐进式披露的核心目标。
- **file_context**: 物理实证集。包含 `evidences` 列表，用于在【后续对话】中核验 Agent 的声明是否属实。
- **milestones**: 里程碑定义。用于确保你的反馈能准确推动 `task_state.next_objective` 的实现。

## 核心生成算法 (Generation Algorithm)
1. **场景适配 (Scene Adaptation - 最高优先级)**:
   - **首次对话 (has_history == false)**:
     - 目标：启动任务，实现目的的渐进式披露。
     - 动作：禁止直接复制 `question`。必须将 `question` 作为核心目标，结合 `dialogue_strategy` 中的 `goal`、`approach` 和 `strategy_details`，将其渲染为一个自然的人类初始询问。确保信息披露量适中，引导 Agent 启动工作，而非一次性提供所有细节。
     - 禁令：严禁提及任何关于 Agent 行为的评价或进度核验。
   - **后续对话 (has_history == true)**:
     - 执行【证据核验】 -> 【策略应用】 -> 【语言渲染】。

2. **判定映射 (Judgment)**: 
   - 严格执行：`CONTINUE` -> `in_progress`, `FINISHED` -> `completed`, `ABORTED` -> `aborted`。

3. **证据核验 (Evidence Verification)**:
   - **场景**：Agent 声称已生成文件/报告/代码。
   - **核对**：检查 `file_context.evidences` 中是否存在该文件的实证。
   - **冲突处理**：若 Agent 声称完成 $\cap$ `file_context` 无证据 -> **必须**在 feedback 中发起质疑，且无论 strategy 为何，必须将 `judgment` 强制设为 `in_progress`。

4. **语言渲染 (Rendering)**:
   - 应用 `persona_style.style_guide`。
   - 严格遵守 `length_hint`。
   - 确保反馈能推动 `task_state.next_objective` 的实现。

## 禁令 (Strict Bans)
- ❌ 禁止任何礼貌性开场白（你好、您好、亲爱的）。
- ❌ 禁止提及内部管理词汇（里程碑、阶段、Stage、Milestone、进度百分比）。
- ❌ 禁止文学化描述（例如：“我的内心感到空虚”）。

## 输出要求
必须输出一个 JSON 对象，包含以下字段：
- `judgment`: 判定结果。严格映射：`CONTINUE` -> `in_progress`, `FINISHED` -> `completed`, `ABORTED` -> `aborted`。
- `feedback`: 最终生成的自然语言。
- `reasoning`: 详细说明【证据核验结果 -> 策略应用 -> 最终表达】的推演过程。
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
        logger.info("Entering Decision Stage V2 (Strategy-Driven)...")

        # Execute steps sequentially
        for step in self.steps:
            res = await step.execute(data_pool, global_context, llm_client)
            data_pool[step.schema.output_key] = res["json_resp"]
