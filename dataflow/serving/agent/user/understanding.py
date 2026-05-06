from typing import Any, Dict, Optional
import logging
from dataflow.core.agentic import (
    LLMClientABC,
    StepSchema,
    UnderstandingResult,
    UserStage,
    UserStep,
    StepResponse,
)

logger = logging.getLogger(__name__)


class UnderstandingStage(UserStage):
    """
    Understanding Stage: Analyzes current task state.
    Contract-driven flow.
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
                    input_keys=["context", "milestones"], output_key="milestone_status"
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="ProgressEvaluator",
                prompt_template=prompts["progress_evaluator"],
                schema=StepSchema(
                    input_keys=["milestone_status", "context"],
                    output_key="progress_assessment",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="TaskSynthesizer",
                prompt_template=prompts["task_synthesizer"],
                schema=StepSchema(
                    input_keys=["milestone_status", "progress_assessment"],
                    output_key="task_state",
                ),
                llm_config=self.llm_config,
            ),
        ]

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ) -> UnderstandingResult:
        logger.info("Entering Understanding Stage...")

        local_pool = data_pool.copy()
        raw_results: dict[str, StepResponse] = {}

        for step in self.steps:
            res = await step.execute(local_pool, global_context, llm_client)
            raw_results[step.name] = res

            val = (
                res.get(step.schema.output_key, res.get("raw_text", ""))
                if "error" not in res
                else res.get("raw_text", "")
            )
            local_pool[step.schema.output_key] = val

        return {
            "task_state": local_pool.get("task_state", {}),
            "intermediate_understanding": {
                "milestone_status": local_pool.get("milestone_status", ""),
                "progress_assessment": local_pool.get("progress_assessment", ""),
            },
            "raw_responses": raw_results,
        }
