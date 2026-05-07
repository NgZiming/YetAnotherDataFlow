from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from dataflow.core.agentic import (
    LLMClientABC,
    StepSchema,
    UserStage,
    UserStep,
)
from dataflow.logger import get_logger

logger = get_logger()

# --- Understanding Stage Models (Pydantic) ---


class MilestoneInfo(BaseModel):
    """单个里程碑的状态信息"""

    milestone_id: str = Field(..., description="里程碑 ID 或名称")
    status: str = Field(
        ...,
        description="完成状态",
        pattern="^(completed|in_progress|not_started|blocked)$",
    )
    completion_percentage: int = Field(
        ..., ge=0, le=100, description="完成百分比 (0-100)"
    )
    reasoning: str = Field(..., description="判断依据")
    blocking_issues: List[str] = Field(default_factory=list, description="阻碍问题列表")


class MilestoneStatus(BaseModel):
    """MilestoneMatcher 的输出：里程碑完成状态"""

    milestones: List[MilestoneInfo] = Field(..., description="每个里程碑的详细状态")
    overall_progress: int = Field(..., ge=0, le=100, description="整体进度 (0-100)")
    summary: str = Field(..., description="综合总结")


class ProgressAssessment(BaseModel):
    """ProgressEvaluator 的输出：进度评估报告"""

    overall_status: str = Field(
        ...,
        description="整体状态",
        pattern="^(on_track|at_risk|behind|completed)$",
    )
    quality_assessment: str = Field(
        ...,
        description="质量评估",
        pattern="^(high|medium|low)$",
    )
    bottlenecks: List[str] = Field(
        default_factory=list, description="阻碍进展的关键问题"
    )
    risks: List[str] = Field(default_factory=list, description="风险因素列表")
    recommendations: List[str] = Field(default_factory=list, description="建议列表")
    detailed_assessment: str = Field(
        ..., min_length=100, max_length=1000, description="详细的评估报告 (300-500 字)"
    )


class TaskState(BaseModel):
    """TaskSynthesizer 的输出：结构化任务状态"""

    current_milestone: str = Field(..., description="当前正在处理的里程碑")
    is_completed: bool = Field(..., description="任务是否已完成")
    completion_reasoning: str = Field(..., description="判断是否完成的依据")
    missing_requirements: List[str] = Field(
        default_factory=list, description="未完成的要求列表"
    )
    next_objective: str = Field(..., description="下一步最优先的目标")
    reasoning: str = Field(
        ..., min_length=50, max_length=500, description="详细的推理过程 (200-300 字)"
    )


class UnderstandingStage(UserStage):
    """
    Understanding Stage: Analyzes current task state.
    Prompts are embedded in StepSchema for self-contained configuration.
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        self.llm_config = llm_config or {}

        # ========== Prompts embedded in StepSchema ==========
        self.steps = [
            UserStep(
                name="MilestoneMatcher",
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
                    prompt_template="""你是一个任务分析专家。分析任务进展，匹配里程碑完成状态。

## 输入
- 任务问题：{question}
- 里程碑列表：{milestones}
- 文件上下文：{file_context}
- Agent 上下文：{agent_context}
- 对话上下文：{dialogue_context}

## 输入字段说明
- **question**: 用户的初始问题/任务描述，用于理解核心需求
- **milestones**: 任务里程碑列表，每个里程碑包含：
  - **stage**: 里程碑阶段编号（如 1, 2, 3）
  - **goal**: 里程碑目标描述
  - **success_criteria**: 完成标准，说明如何判断该里程碑已完成
  - **required_clues**: 完成该里程碑所需的线索/信息列表
  - **used_skills**: 已使用的技能列表
- **file_context**: 文件总结，说明任务相关的代码/配置/数据结构
- **agent_context**: Agent 行动总结，包含 key_actions, key_findings, reasoning_pattern
- **dialogue_context**: 对话上下文，包含 user_intent, emotional_tone, has_history

## 输出字段说明
- **milestones**: 每个里程碑的详细状态列表
  - **milestone_id**: 里程碑 ID（如 "stage_1", "stage_2"）
  - **status**: 完成状态（必须从以下值中选择一个）
    - **completed**: 已完成，满足所有要求
    - **in_progress**: 进行中，正在处理
    - **not_started**: 未开始，尚未处理
    - **blocked**: 被阻塞，有阻碍问题无法继续
  - **completion_percentage**: 完成百分比（0-100）
  - **reasoning**: 判断依据，说明为什么是这个状态
  - **blocking_issues**: 阻碍问题列表（如果 status 是 blocked）

- **overall_progress**: 整体进度（0-100），基于所有里程碑的平均进度
- **summary**: 综合总结（100-200 字），概括任务整体进展

## 任务
1. **理解任务目标**: 从 question 中理解用户的核心需求
2. **理解里程碑**: 分析每个里程碑的具体要求和验收标准
3. **评估完成度**: 结合 file_context、agent_context、dialogue_context 判断每个里程碑的完成状态
4. **识别依赖关系**: 发现里程碑之间的依赖关系

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown 代码块）
- 只输出纯 JSON，不要任何解释文字
- 必须包含所有字段
- 每个里程碑必须有 status 和 completion_percentage

## 输出示例
{{
  "milestones": [
    {{
      "milestone_id": "stage_1",
      "status": "in_progress",
      "completion_percentage": 60,
      "reasoning": "已搜索 composer X 的基本信息，但尚未分析其代表作品",
      "blocking_issues": []
    }},
    {{
      "milestone_id": "stage_2",
      "status": "not_started",
      "completion_percentage": 0,
      "reasoning": "等待 stage_1 完成后才能开始",
      "blocking_issues": ["需要完成 stage_1 才能继续"]
    }}
  ],
  "overall_progress": 30,
  "summary": "任务处于初步探索阶段，已完成 60% 的 stage_1（基本信息收集），stage_2 尚未开始。整体进度约 30%。"
}}""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="ProgressEvaluator",
                schema=StepSchema(
                    input_keys=[
                        "milestone_status",
                        "file_context",
                        "agent_context",
                        "dialogue_context",
                    ],
                    output_key="progress_assessment",
                    output_type=ProgressAssessment,
                    prompt_template="""你是一个项目评估专家。评估任务整体进度，识别关键问题。

## 输入
- 里程碑状态：{milestone_status}
- 文件上下文：{file_context}
- Agent 上下文：{agent_context}
- 对话上下文：{dialogue_context}

## 输入字段说明
- **milestone_status**: 里程碑状态总结，包含：
  - **milestones**: 每个里程碑的 status 和 completion_percentage
  - **overall_progress**: 整体进度（0-100）
  - **summary**: 综合总结

- **file_context**: 文件总结，说明任务相关的代码/配置/数据结构

- **agent_context**: Agent 行动总结，包含：
  - **key_actions**: Agent 执行的关键行动
  - **key_findings**: Agent 获得的重要发现
  - **reasoning_pattern**: 推理模式

- **dialogue_context**: 对话上下文，包含：
  - **user_intent**: 用户意图
  - **emotional_tone**: 用户情感倾向
  - **key_questions**: 用户提出的问题
  - **has_history**: 是否有历史对话

## 输出字段说明
- **overall_status**: 整体状态（必须从以下值中选择一个）
  - **on_track**: 进展顺利，按计划进行
  - **at_risk**: 有风险，可能出现延误
  - **behind**: 落后于计划，需要加速
  - **completed**: 任务已完成

- **quality_assessment**: 质量评估（必须从以下值中选择一个）
  - **high**: 高质量，超出预期
  - **medium**: 中等质量，满足基本要求
  - **low**: 低质量，需要改进

- **bottlenecks**: 阻碍进展的关键问题列表（3-5 个）
  - 从 agent_context 和 dialogue_context 中识别

- **risks**: 可能影响后续进展的风险因素列表（2-4 个）
  - 预测未来可能出现的问题

- **recommendations**: 改进建议列表（2-4 个）
  - 针对 bottlenecks 和 risks 提出具体建议

- **detailed_assessment**: 详细的评估报告（100-500 字）
  - 整合所有信息，全面评估任务进展

## 任务
1. **整体进度评估**: 基于 milestone_status 判断任务的总体完成度
2. **识别瓶颈**: 从 agent_context 和 dialogue_context 中找出阻碍进展的关键问题
3. **评估质量**: 判断已完成工作的质量（是否满足要求）
4. **预测风险**: 识别可能影响后续进展的风险因素

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown 代码块）
- 只输出纯 JSON，不要任何解释文字
- 必须包含所有字段
- overall_status 和 quality_assessment 必须严格使用指定的枚举值

## 输出示例
{{
  "overall_status": "on_track",
  "quality_assessment": "medium",
  "bottlenecks": [
    "缺少 composer X 的完整作品列表",
    "需要更多关于 progressive house 的历史背景信息"
  ],
  "risks": [
    "可能找不到足够详细的音乐风格分析",
    "Agent 可能需要多次搜索才能找到准确信息"
  ],
  "recommendations": [
    "优先搜索官方音乐数据库获取作品列表",
    "参考权威音乐评论网站获取风格分析"
  ],
  "detailed_assessment": "任务整体进展顺利，已完成 60% 的初步信息收集。Agent 已搜索到 composer X 的基本信息和音乐风格，但缺少完整的作品列表和详细的历史背景。质量中等，满足基本要求但需要进一步补充。建议优先搜索官方音乐数据库，参考权威评论网站获取更准确的信息。"
}}""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="TaskSynthesizer",
                schema=StepSchema(
                    input_keys=[
                        "milestone_status",
                        "progress_assessment",
                        "dialogue_context",
                    ],
                    output_key="task_state",
                    output_type=TaskState,
                    prompt_template="""你是一个任务状态合成器。整合评估结果，生成结构化的任务状态。

## 输入
- 里程碑状态：{milestone_status}
- 进度评估：{progress_assessment}
- 对话上下文：{dialogue_context}

## 输入字段说明
- **milestone_status**: 里程碑状态总结，包含：
  - **milestones**: 每个里程碑的 status 和 completion_percentage
  - **overall_progress**: 整体进度（0-100）
  - **summary**: 综合总结

- **progress_assessment**: 进度评估报告，包含：
  - **overall_status**: 整体状态（on_track/at_risk/behind/completed）
  - **quality_assessment**: 质量评估（high/medium/low）
  - **bottlenecks**: 瓶颈列表
  - **risks**: 风险列表
  - **recommendations**: 建议列表
  - **detailed_assessment**: 详细评估报告

- **dialogue_context**: 对话上下文，包含：
  - **user_intent**: 用户意图
  - **emotional_tone**: 用户情感倾向
  - **key_questions**: 用户提出的问题
  - **has_history**: 是否有历史对话（**关键：用于判断首次对话**）

## 输出字段说明
- **current_milestone**: 当前正在处理的里程碑
  - **首次对话**: "stage_1"
  - **后续对话**: 根据 milestone_status 判断，如 "stage_2"

- **is_completed**: 任务是否已完成
  - **true**: 所有里程碑都已完成
  - **false**: 任务仍在进行中

- **completion_reasoning**: 判断是否完成的依据
  - 说明为什么是 completed 或 not completed

- **missing_requirements**: 未完成的要求列表
  - 如果 is_completed=false，列出未完成的要求
  - 如果 is_completed=true，为空列表

- **next_objective**: 下一步最优先的目标
  - **首次对话**: "等待用户提出初始问题，启动任务"
  - **后续对话**: 根据 progress_assessment 和 recommendations 提出具体目标

- **reasoning**: 详细的推理过程（100-500 字）
  - 整合所有信息，说明任务当前状态和下一步方向

## 任务
1. **判断是否首次对话**: 检查 dialogue_context.has_history
   - **false**: 首次对话，任务尚未启动
   - **true**: 后续对话，任务已在进行中

### 场景 A：首次对话（has_history == false）
任务状态：
- `current_milestone`: "stage_1"
- `is_completed`: false
- `next_objective`: "等待用户提出初始问题，启动任务"
- **不要**催促用户，而是等待用户主动提问

### 场景 B：后续对话（has_history == true）
任务状态：
- 根据 milestone_status 判断当前进展
- 如果进展缓慢，可以标记需要催促
- 根据 progress_assessment 判断是否需要调整方向

## 输出要求
- 格式：严格的 JSON 格式（不要 Markdown 代码块）
- 只输出纯 JSON，不要任何解释文字
- 必须包含所有字段
- 首次对话时，next_objective 必须说明"等待用户提问"

## 输出示例（首次对话）
{{
  "current_milestone": "stage_1",
  "is_completed": false,
  "completion_reasoning": "任务尚未启动，等待用户提出初始问题",
  "missing_requirements": [],
  "next_objective": "等待用户提出初始问题，启动探索任务",
  "reasoning": "这是首次对话，用户尚未提出具体问题。任务处于初始状态，所有里程碑都未开始。下一步是等待用户提出问题，然后根据问题启动相应的探索任务."
}}

## 输出示例（后续对话）
{{
  "current_milestone": "stage_2",
  "is_completed": false,
  "completion_reasoning": "stage_1 已完成（基本信息收集），但 stage_2 仍在进行中（代表作品分析）",
  "missing_requirements": ["需要完整的作品列表", "需要分析每部作品的音乐风格特点"],
  "next_objective": "搜索 composer X 的完整作品列表，并分析其代表作品的音乐风格",
  "reasoning": "根据 milestone_status，stage_1 已完成 100%，stage_2 已完成 40%。progress_assessment 显示整体进展顺利（on_track），质量中等。下一步应该继续搜索作品列表，参考权威音乐数据库获取准确信息。用户希望获得更详细的音乐风格分析，这是当前重点。"
}}""",
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
        logger.info("Entering Understanding Stage...")

        # 预先检查所有步骤的输入依赖
        self._check_step_dependencies(self.steps, data_pool, "UnderstandingStage")

        # 执行步骤
        for step in self.steps:
            res = await step.execute(data_pool, global_context, llm_client)
            data_pool[step.schema.output_key] = res["json_resp"]

        logger.info(f"MilestoneStatus: {data_pool['milestone_status']}")
        logger.info(f"ProgressAssessment: {data_pool['progress_assessment']}")
        logger.info(f"TaskState: {data_pool['task_state']}")
