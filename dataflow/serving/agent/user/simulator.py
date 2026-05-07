from typing import Any, Dict, Optional

from dataflow.core.agentic import UserSimulatorABC, LLMClientABC, SimulationResult
from dataflow.logger import get_logger

from .perception import PerceptionStage
from .understanding import UnderstandingStage
from .decision import DecisionStage

logger = get_logger()


class UserSimulator(UserSimulatorABC):
    def __init__(
        self,
        llm_client: LLMClientABC,
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.llm_config = llm_config or {}
        self.llm_client = llm_client

        # ✅ Stage 自包含 prompts，无需外部传入
        self.perception_stage = PerceptionStage(self.llm_config)
        self.understanding_stage = UnderstandingStage(self.llm_config)
        self.decision_stage = DecisionStage(self.llm_config)

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
        logger.info("=== Stage 1: Perception ===")
        await self.perception_stage.execute(
            data_pool,
            global_context,
            self.llm_client,
        )

        # --- Stage 2: Understanding ---
        logger.info("=== Stage 2: Understanding ===")
        await self.understanding_stage.execute(
            data_pool,
            global_context,
            self.llm_client,
        )

        # --- Stage 3: Decision ---
        logger.info("=== Stage 3: Decision ===")
        await self.decision_stage.execute(
            data_pool,
            global_context,
            self.llm_client,
        )

        # 组装最终结果
        return {
            "final_response": data_pool.get("final_response", {}),
            "global_context": global_context,
        }
