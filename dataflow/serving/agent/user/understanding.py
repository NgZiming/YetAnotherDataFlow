from typing import Any, Dict, Optional
import logging
from dataflow.core.agentic import (
    LLMClientABC,
    StepSchema,
    UserStage,
    UserStep,
)
from dataflow.core.agentic.user import (
    MilestoneStatus,
    ProgressAssessment,
    TaskStatePydantic,
)

logger = logging.getLogger(__name__)


class UnderstandingStage(UserStage):
    """
    Understanding Stage: Analyzes current task state.
    Contract-driven flow with pre-check dependency validation and Pydantic models.
    """

    def __init__(
        self,
        prompts: Dict[str, str],
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.llm_config = llm_config or {}

        self.steps = [
            UserStep(
                name="MilestoneMatcher",
                prompt_template=prompts["milestone_matcher"],
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
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="ProgressEvaluator",
                prompt_template=prompts["progress_evaluator"],
                schema=StepSchema(
                    input_keys=[
                        "milestone_status",
                        "file_context",
                        "agent_context",
                        "dialogue_context",
                    ],
                    output_key="progress_assessment",
                    output_type=ProgressAssessment,
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="TaskSynthesizer",
                prompt_template=prompts["task_synthesizer"],
                schema=StepSchema(
                    input_keys=["milestone_status", "progress_assessment"],
                    output_key="task_state",
                    output_type=TaskStatePydantic,
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

        # ========== 预先检查所有步骤的输入依赖 ==========
        self._check_step_dependencies(self.steps, data_pool, "UnderstandingStage")

        # ========== 执行步骤 ==========
        for step in self.steps:
            # Execute the step
            res = await step.execute(data_pool, global_context, llm_client)
            data_pool[step.schema.output_key] = res["json_resp"]

        logger.info(f"MilestoneStatus: {data_pool['milestone_status']}")
        logger.info(f"ProgressAssessment: {data_pool['progress_assessment']}")
        logger.info(f"TaskState: {data_pool['task_state']}")
