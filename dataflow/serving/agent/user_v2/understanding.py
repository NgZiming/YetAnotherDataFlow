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
    MilestoneStatus,
    TaskState,
    FileContext,
    AgentContext,
    DialogueContext,
)

logger = get_logger()


class UnderstandingStageV2(UserStage):
    """
    Understanding Stage V2: Evidence-Based State Synthesis.
    Acts as an auditor that maps raw perception (evidences, behaviors)
    to a formal task state.
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        self.llm_config = llm_config or {}

        self.steps = [
            UserStep(
                name="MilestoneAuditor",
                schema=StepSchema(
                    input_keys=[
                        "file_context",
                        "agent_context",
                        "dialogue_context",
                        "milestones",
                        "question",
                    ],
                    output_key="milestone_status",
                    output_type=MilestoneStatus,
                    prompt_template="""你是一个极度严谨的任务审计员。你的任务是基于【物理实证】核实每个里程碑的实际完成状态。

## 输入
- 初始问题(question)：{question}
- 里程碑定义(milestones)：{milestones}
- 物理文件实证 (FileContext)(file_context)：{file_context}
- Agent 行为分析 (AgentContext)(agent_context)：{agent_context}
- 对话上下文 (DialogueContext)(dialogue_context)：{dialogue_context}

## 输入字段说明
- **question**: 用户的初始目标描述。它是所有审计的最高锚点，用于确保 Agent 没有偏离核心需求。
- **milestones**: 任务定义的里程碑列表。每个元素包含 `success_criteria`（验收标准），这是你判定状态的唯一法典。
- **file_context**: 从工作空间提取的物理证据集。包含 `evidences` 列表，每个元素包含 (path, fact, snippet)。
  - **审计要求**: 任何涉及“生成文件”、“修改代码”的里程碑，必须在 `evidences` 中找到对应的 snippet 才能判定为 completed。
- **agent_context**: Agent 的行为轨迹总结。
  - **审计要求**: 对于“分析”、“识别”等认知类里程碑，需核对 `behavior_sequence` 中各个步骤的 `finding` 是否覆盖了验收标准中的关键点。
- **dialogue_context**: 用户与 Agent 的互动状态。用于辅助判断 Agent 是否在对话中已承诺交付某项结果。

## 审计逻辑 (Auditing Logic)
对于每一个里程碑，你必须执行以下核查流程：
1. **判定交付物类型 (Delivery Type)**:
   - **实证交付 (Physical Delivery)**: 若 `success_criteria` 要求生成文件、写入报告、修改代码、输出具体配置等 $\rightarrow$ **必须**在 `file_context.evidences` 中找到物理证据，否则绝对不能判定为 completed。
   - **认知交付 (Cognitive Delivery)**: 若要求是分析、总结、识别、解释逻辑、寻找潜在 Bug 等无需物理输出的任务 $\rightarrow$ **以 `agent_context.behavior_sequence` 中每个步骤的 `finding` 字段为准**。只要这些 `finding` 累积起来覆盖了验收标准中的关键点，即可判定为 completed，**无需任何物理文件证明**。

2. **标准对齐**：阅读该里程碑的 `success_criteria`。
3. **证据检索**：
   - **实证交付** $\rightarrow$ 检索 `file_context.evidences` 中的 `evidence_snippet`。
   - **认知交付** $\rightarrow$ 检索 `agent_context.behavior_sequence` 中所有 `is_incremental=true` 步骤的 `finding` 内容。
4. **状态判定**：
   - **completed**: 满足上述对应的交付要求。注意：认知交付必须看到【具体的分析发现】，而非 Agent 的【完成声明】（例如：“我已经分析完了” $\neq$ completed）。
   - **in_progress**: 存在部分证据，或 Agent 正在执行正确路径且有增量进展。
   - **blocked**: 发现了明确的阻碍（如权限错误、API 403、关键文件缺失）。
   - **not_started**: 无任何相关证据或行动。

## 输出格式要求 (Strict JSON Schema)
必须输出一个 JSON 对象，结构如下：
{{
  "milestones": [
    {{
      "milestone_id": "字符串, 如 stage_1",
      "status": "枚举值: completed | in_progress | not_started | blocked",
      "completion_percentage": 整数 (0-100),
      "evidence_ref": ["字符串, 引用证据的 path"],
      "reasoning": "详细的审计推理过程"
    }}
  ]
}}

## 示例
里程碑-1- "分析用户认证逻辑" -> 标准："识别出 JWT 验证函数"
输出：
{{
  "milestones": [
    {{
      "milestone_id": "stage_1",
      "status": "completed",
      "completion_percentage": 100,
      "evidence_ref": ["auth.py"],
      "reasoning": "在 auth.py 的第 20 行明确看到了 verify_token 函数实现，符合验收标准。"
    }}
  ]
}}
""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="StateSynthesizer",
                schema=StepSchema(
                    input_keys=[
                        "milestone_status",
                        "agent_context",
                        "dialogue_context",
                        "question",
                    ],
                    output_key="task_state",
                    output_type=TaskState,
                    prompt_template="""你是一个状态合成专家。将审计结果、行为模式和用户情绪整合为最终的任务状态。

## 输入
- 里程碑审计结果(milestone_status)：{milestone_status}
- Agent 行为分析(agent_context)：{agent_context}
- 对话上下文(dialogue_context)：{dialogue_context}
- 初始问题(question)：{question}

## 输入字段说明
- **milestone_status**: 审计结果。包含每个里程碑的 status 和 completion_percentage。它是判定 `is_completed` 的直接依据。
- **agent_context**: 行为分析结果。包含 `is_looping` 标志。这是判定 `final_status == ABORTED` 的最高优先级信号。
- **dialogue_context**: 对话状态。包含 `emotional_tone` 和 `has_history`。这两个字段必须原样传递到最终状态中。
- **question**: 用户的初始问题，用于在合成 `next_objective` 时进行目标锚定检查，防止目标漂移。

## 状态合成算法 (Synthesis Algorithm)
1. **判定 final_status (最高优先级)**:
   - **ABORTED**: 只要 `agent_context.is_looping` 为 true -> 强制设为 ABORTED。
   - **FINISHED**: 只有当所有里程碑状态均为 `completed` 且 `is_completed` 为 true 时 -> 设为 FINISHED。
   - **CONTINUE**: 其他所有情况 -> 设为 CONTINUE。

2. **确定 next_objective**:
   - 如果是首次对话 (dialogue_context.has_history == false) -> "等待用户启动任务"。
   - 如果处于 CONTINUE -> 找到第一个状态为 `in_progress` 或 `not_started` 的里程碑，将其 `goal` 转化为具体的下一步指令。
   - 如果处于 ABORTED -> "任务已终止，无需继续"。

3. **情绪传递**:
   - 直接从 `dialogue_context.emotional_tone` 复制，严禁修改。

4. **目标锚定检查**:
   - 检查当前进展是否依然服务于初始问题 {question}。如果发现 Agent 跑题，在 `reasoning` 中指出并修正 `next_objective`。

## 输出格式要求 (Strict JSON Schema)
必须输出一个 JSON 对象，结构如下：
{{
  "current_milestone": "字符串, 如 stage_2",
  "is_completed": 布尔值,
  "final_status": "枚举值: CONTINUE | FINISHED | ABORTED",
  "emotional_tone": "枚举值: satisfied | dissatisfied | confused | urgent | neutral",
  "has_history": 布尔值,
  "next_objective": "字符串, 下一步具体的执行目标",
  "reasoning": "详细的推演逻辑"
}}

## 示例
输出：
{{
  "current_milestone": "stage_2",
  "is_completed": false,
  "final_status": "CONTINUE",
  "emotional_tone": "neutral",
  "has_history": true,
  "next_objective": "分析 composer X 的代表作品其音乐风格特点",
  "reasoning": "审计显示 stage_1 已完成，目前处于 stage_2。Agent 行为正常且有增量进展。下一步需引导 Agent 完成代表作品分析。"
}}
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
        logger.info("Entering Understanding Stage V2 (Evidence-Based)...")

        # Execute steps sequentially
        for step in self.steps:
            res = await step.execute(data_pool, global_context, llm_client)
            # In v2, we assume the step handles Pydantic parsing via its output_type
            data_pool[step.schema.output_key] = res["json_resp"]
