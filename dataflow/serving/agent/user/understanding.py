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

## 任务
1. **理解任务目标**: 从 {question} 中理解用户的核心需求
2. **理解里程碑**: 分析每个里程碑的具体要求和验收标准
3. **评估完成度**: 结合 file_context、agent_context、dialogue_context 判断每个里程碑的完成状态
4. **识别依赖关系**: 发现里程碑之间的依赖关系

## 输出要求
- 格式：JSON 格式
- 必须包含字段：
{{
  "milestones": [
    {{
      "milestone_id": "里程碑 ID 或名称",
      "status": "completed|in_progress|not_started|blocked",
      "completion_percentage": 0-100,
      "reasoning": "判断依据",
      "blocking_issues": ["阻碍问题 1", ...]
    }}
  ],
  "overall_progress": 0-100,
  "summary": "综合总结"
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

## 任务
1. **整体进度评估**: 基于 milestone_status 判断任务的总体完成度
2. **识别瓶颈**: 从 agent_context 和 dialogue_context 中找出阻碍进展的关键问题
3. **评估质量**: 判断已完成工作的质量（是否满足要求）
4. **预测风险**: 识别可能影响后续进展的风险因素

## 输出要求
- 格式：JSON 格式
- 必须包含字段：
{{
  "overall_status": "on_track|at_risk|behind|completed",
  "quality_assessment": "high|medium|low",
  "bottlenecks": ["瓶颈 1", "瓶颈 2", ...],
  "risks": ["风险 1", "风险 2", ...],
  "recommendations": ["建议 1", "建议 2", ...],
  "detailed_assessment": "详细的评估报告，300-500 字"
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

## 重要：判断是否为首次对话
**首次对话检测**：
- 如果 `dialogue_context.has_history == false`：这是首次对话，任务尚未启动
- 如果 `dialogue_context.has_history == true`：这是后续对话，任务已在进行中

### 场景 A：首次对话（has_history == false）
任务状态：
- `current_milestone`: "stage_1"（第一个里程碑）
- `is_completed`: false
- `next_objective`: "等待用户提出初始问题，启动任务"
- 不要催促用户，而是等待用户主动提问

### 场景 B：后续对话（has_history == true）
任务状态：
- 根据 milestone_status 判断当前进展
- 如果进展缓慢，可以标记需要催促
- 根据 progress_assessment 判断是否需要调整方向

## 任务
1. **判断是否首次对话**: 检查 dialogue_context.has_history
2. **整合信息**: 合并 milestone_status 和 progress_assessment
3. **确定当前阶段**: 判断任务当前处于哪个阶段
4. **生成下一步建议**: 提出最优先的下一步行动
5. **判断是否完成**: 综合判断任务是否已完成

## 输出要求
- 格式：JSON 格式
- 必须包含字段：
{{
  "current_milestone": "当前正在处理的里程碑（首次对话时为 stage_1）",
  "is_completed": true|false,
  "completion_reasoning": "判断是否完成的依据",
  "missing_requirements": ["未完成的要求 1", ...],
  "next_objective": "下一步最优先的目标（首次对话时说明等待用户提问）",
  "reasoning": "详细的推理过程，200-300 字"
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
