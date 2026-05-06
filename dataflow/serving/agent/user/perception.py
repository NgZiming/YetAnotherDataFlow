import logging

from typing import Any, Dict, Optional
from dataflow.core.agentic import (
    LLMClientABC,
    PerceptionResult,
    StepSchema,
    UserStage,
    UserStep,
    StepResponse,
)

logger = logging.getLogger(__name__)


class PerceptionStage(UserStage):
    """
    Perception Stage: Compresses raw data into structured context.
    Now uses a Step-based registry for automatic data flow.
    """

    def __init__(
        self,
        prompts: Dict[str, str],
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.llm_config = llm_config or {}

        # Define steps as a list to maintain execution order
        # Each step defines its own contract: (What it needs, What it produces)
        self.steps = [
            UserStep(
                name="FileSensor",
                prompt_template=prompts["file_sensor"],
                schema=StepSchema(
                    input_keys=["file_contents"], output_key="file_context"
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="AgentSensor",
                prompt_template=prompts["agent_sensor"],
                schema=StepSchema(
                    input_keys=["file_context", "agent_outputs"],
                    output_key="agent_context",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="DialogueSensor",
                prompt_template=prompts["dialogue_sensor"],
                schema=StepSchema(
                    input_keys=["feedbacks", "agent_context", "file_context"],
                    output_key="dialogue_context",
                ),
                llm_config=self.llm_config,
            ),
        ]

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ) -> PerceptionResult:
        logger.info("Entering Perception Stage...")

        # Local pool for this stage, initialized with input data
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
            "context": {
                "file_context": local_pool.get("file_context", ""),
                "agent_context": local_pool.get("agent_context", ""),
                "dialogue_context": local_pool.get("dialogue_context", ""),
            },
            "raw_responses": raw_results,
        }
