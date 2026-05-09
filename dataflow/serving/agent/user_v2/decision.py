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
                    prompt_template="""你是一个对话策略专家。根据审计后的任务状态，制定下一步的对话战略。

## 输入
- 任务状态：{task_state}
- 对话脚本：{dialogue_scripts}

## 战略决策矩阵 (Strategy Matrix)
你必须严格根据 `task_state.final_status` 和 `has_history` 选择策略：

1. **首次对话 (has_history == false)**:
   - 策略：【提问】
   - 目标：提出清晰的初始问题，启动任务。

2. **任务终止 (final_status == ABORTED)**:
   - 策略：【其他】
   - 目标：直接且决绝地通知 Agent 任务终止，无需继续。

3. **任务完成 (final_status == FINISHED)**:
   - 策略：【表示满意】
   - 目标：确认所有目标已达成，表达认可并结束对话。

4. **任务进行中 (final_status == CONTINUE)**:
   - 如果 Agent 响应缓慢/重复 $\rightarrow$ 【催促】
   - 如果 Agent 回答有误/缺失 $\rightarrow$ 【表达不满】或【要求澄清】
   - 如果 Agent 进展顺利 $\rightarrow$ 【提供反馈】（引导至 next_objective）

## 输出格式要求 (Strict JSON Schema)
必须输出一个 JSON 对象，结构如下：
{{
  "strategy_type": "枚举值: 提问 | 提供反馈 | 要求澄清 | 表达不满 | 表示满意 | 催促 | 其他",
  "goal": "本次对话要达成的具体目的",
  "approach": "建议的表达方式 (例如：直接质疑、温和引导、果断终止)",
  "strategy_rationale": "选择该策略的原因",
  "strategy_details": "详细的策略描述"
}}

## 示例
输出：
{{
  "strategy_type": "提供反馈",
  "goal": "引导 Agent 补充缺失的认证函数细节",
  "approach": "指出目前结论过于笼统，要求提供具体的代码行号",
  "strategy_rationale": "Agent 虽识别了认证模块，但未提供具体实现细节",
  "strategy_details": "用户注意到 Agent 提到的 verify_token 函数在摘要中没有具体逻辑，需要引导其深入阅读代码并给出结论。"
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

## 风格映射逻辑
1. **人设提取**：分析 `user_persona` 提取核心性格特质（Traits）。
2. **语气对齐**：结合 `task_state.emotional_tone` 和 `final_status` 确定语气（Tone）。
   - ABORTED + dissatisfied $\rightarrow$ 冰冷、果断。
   - FINISHED + satisfied $\rightarrow$ 热情、赞赏。
   - CONTINUE + neutral $\rightarrow$ 简洁、高效。
3. **构建 Style Guide**：为本次回制制定一套明确的 "Do's and Don'ts"。
   - **通用 Don'ts**: 禁止礼貌开场白（你好/您好）、禁止文学化修辞、禁止提及内部术语（里程碑/阶段）。
   - **人设 Do's**: 根据 traits 添加特定的说话习惯（例如：专业人士 $\rightarrow$ 多用术语，简洁直接）。

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

## 核心生成算法 (Generation Algorithm)
1. **判定映射 (Judgment)**: 
   - 严格执行：`CONTINUE` $\rightarrow$ `in_progress`, `FINISHED` $\rightarrow$ `completed`, `ABORTED` $\rightarrow$ `aborted`。

2. **证据核验 (Evidence Verification - 最高优先级)**:
   - **场景**：Agent 声称已生成文件/报告/代码。
   - **核对**：检查 `file_context.evidences` 中是否存在该文件的实证。
   - **冲突处理**：若 Agent 声称完成 $\cap$ `file_context` 无证据 $\rightarrow$ **必须**在 feedback 中发起质疑（例如：“你说文件写好了，但我没看到，请确认路径。”），且无论 strategy 为何，必须将 `judgment` 强制设为 `in_progress`。

3. **语言渲染 (Rendering)**:
   - 应用 `persona_style.style_guide`。
   - 严格遵守 `length_hint`。
   - 确保反馈能推动 `task_state.next_objective` 的实现。

## 禁令 (Strict Bans)
- ❌ 禁止任何礼貌性开场白（你好、您好、亲爱的）。
- ❌ 禁止提及内部管理词汇（里程碑、阶段、Stage、Milestone、进度百分比）。
- ❌ 禁止文学化描述（例如：“我的内心感到空虚”）。

## 输出要求
输出一个 `FinalResponse` JSON 对象。
- `judgment`: 判定结果。严格映射：`CONTINUE` $\rightarrow$ `in_progress`, `FINISHED` $\rightarrow$ `completed`, `ABORTED` $\rightarrow$ `aborted`。
- `feedback`: 最终生成的自然语言。
- `reasoning`: 详细说明【证据核验结果 $\rightarrow$ 策略应用 $\rightarrow$ 最终表达】的推演过程。
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
