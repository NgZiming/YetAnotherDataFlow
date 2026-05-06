import logging

from typing import Any, Dict, Optional
from dataflow.core.agentic import UserSimulatorABC, LLMClientABC, SimulationResult

from .perception import PerceptionStage
from .understanding import UnderstandingStage
from .decision import DecisionStage

logger = logging.getLogger(__name__)


class UserSimulator(UserSimulatorABC):
    def __init__(
        self,
        prompts: Dict[str, Dict[str, str]],
        llm_client: LLMClientABC,
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.llm_config = llm_config or {}
        self.llm_client = llm_client

        self.perception_stage = PerceptionStage(
            prompts.get("perception", {}),
            self.llm_config,
        )
        self.understanding_stage = UnderstandingStage(
            prompts.get("understanding", {}),
            self.llm_config,
        )
        self.decision_stage = DecisionStage(
            prompts.get("decision", {}),
            self.llm_config,
        )

    async def run(
        self,
        raw_data: Dict[str, Any],
        global_context: Optional[Dict[str, Any]] = None,
    ) -> SimulationResult:
        if self.llm_client is None:
            raise RuntimeError("LLM client must be provided to UserSimulator.")

        global_context = global_context or {}

        # The data_pool acts as a shared blackboard for the current turn
        data_pool = raw_data.copy()

        # --- Stage 1: Perception ---
        perception_result = await self.perception_stage.execute(
            data_pool,
            global_context,
            self.llm_client,
        )
        # Update data_pool with the structured context
        data_pool.update(perception_result.get("context", {}))

        # --- Stage 2: Understanding ---
        understanding_result = await self.understanding_stage.execute(
            data_pool,
            global_context,
            self.llm_client,
        )
        # Update data_pool with the task state
        data_pool.update({"task_state": understanding_result.get("task_state", {})})

        # --- Stage 3: Decision ---
        decision_result = await self.decision_stage.execute(
            data_pool,
            global_context,
            self.llm_client,
        )

        return {
            "final_response": decision_result["final_response"],
            "trajectory": {
                "perception": perception_result,
                "understanding": understanding_result,
                "decision": decision_result,
            },
            "global_context": global_context,
        }
