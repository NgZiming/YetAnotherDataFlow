import asyncio
import logging

from typing import Any, Dict, Optional
from dataflow.core.agentic import (
    DecisionResult,
    LLMClientABC,
    StepSchema,
    UserStage,
    UserStep,
    StepResponse,
)

logger = logging.getLogger(__name__)


class DecisionStage(UserStage):
    """
    Decision Stage: Parallel Strategy + Persona -> Final Response.
    """

    def __init__(
        self,
        prompts: Dict[str, str],
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.llm_config = llm_config or {}

        # Parallel steps
        self.strategizer = UserStep(
            name="Strategizer",
            prompt_template=prompts["strategizer"],
            schema=StepSchema(
                input_keys=["task_state", "dialogue_scripts"],
                output_key="dialogue_strategy",
            ),
            llm_config=self.llm_config,
        )
        self.persona_adapter = UserStep(
            name="PersonaAdapter",
            prompt_template=prompts["persona_adapter"],
            schema=StepSchema(
                input_keys=["task_state", "user_persona"], output_key="persona_context"
            ),
            llm_config=self.llm_config,
        )
        # Synthesis step
        self.response_generator = UserStep(
            name="ResponseGenerator",
            prompt_template=prompts["response_generator"],
            schema=StepSchema(
                input_keys=["dialogue_strategy", "persona_context", "task_state"],
                output_key="final_response",
            ),
            llm_config=self.llm_config,
        )

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ) -> DecisionResult:
        logger.info("Entering Decision Stage...")

        # 1. Parallel execution
        strategy_task = self.strategizer.execute(data_pool, global_context, llm_client)
        persona_task = self.persona_adapter.execute(
            data_pool, global_context, llm_client
        )

        strategy_res, persona_res = await asyncio.gather(strategy_task, persona_task)

        # 2. Feed parallel results into the pool for the final synthesis step
        local_pool = data_pool.copy()
        dialogue_strategy: str = strategy_res.get(
            "dialogue_strategy", strategy_res.get("raw_text", "")
        )
        local_pool["dialogue_strategy"] = dialogue_strategy
        persona_context: str = persona_res.get(
            "persona_context", persona_res.get("raw_text", "")
        )
        local_pool["persona_context"] = persona_context

        # 3. Final Synthesis
        gen_res = await self.response_generator.execute(
            local_pool, global_context, llm_client
        )
        final_response: str = gen_res.get("final_response", gen_res.get("raw_text", ""))

        return {
            "final_response": final_response,
            "decision_details": {
                "dialogue_strategy": dialogue_strategy,
                "persona_context": persona_context,
            },
            "raw_responses": {
                "strategizer": strategy_res,
                "persona_adapter": persona_res,
                "response_generator": gen_res,
            },
        }
