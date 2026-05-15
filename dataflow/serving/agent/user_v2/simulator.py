import re

from datetime import datetime
from typing import Any, Dict, List, Optional
from dataflow.logger import get_logger
from dataflow.core.agentic import LLMClientABC
from dataflow.serving.agent.user.simulator import UserSimulatorABC, SimulationResult

from .perception import PerceptionStageV2
from .understanding import UnderstandingStageV2
from .decision import DecisionStageV2
from .models import FinalResponse

logger = get_logger()


class UserSimulator(UserSimulatorABC):
    """
    UserSimulator v2.0: Evidence-Driven Cognitive Architecture.

    Orchestrates the flow:
    Perception (Evidence) -> Understanding (Auditing) -> Decision (Strategizing & Rendering)
    """

    def __init__(
        self, llm_client: LLMClientABC, llm_config: Optional[Dict[str, Any]] = None
    ):
        self.llm_client = llm_client
        self.llm_config = llm_config or {}

        # Initialize the cognitive pipeline
        self.perception_stage = PerceptionStageV2(llm_config=self.llm_config)
        self.understanding_stage = UnderstandingStageV2(llm_config=self.llm_config)
        self.decision_stage = DecisionStageV2(llm_config=self.llm_config)

        logger.info("UserSimulator v2.0 initialized with Evidence-Driven pipeline.")

    def run(
        self,
        raw_data: Dict[str, Any],
        global_context: Dict[str, Any],
    ) -> SimulationResult:
        """
        Main execution loop of the simulator, following UserSimulatorABC.
        """

        try:
            # Stage 1: Perception
            self.perception_stage.execute(raw_data, global_context, self.llm_client)

            # Stage 2: Understanding
            self.understanding_stage.execute(raw_data, global_context, self.llm_client)

            dialogue_scripts = global_context.get("dialogue_scripts")
            task_state = raw_data.get("task_state")

            user_persona = None
            if dialogue_scripts and task_state:
                current_milestone = task_state.get("current_milestone", "")
                stage_match = re.search(
                    r"stage_(\d+)", str(current_milestone), re.IGNORECASE
                )

                if stage_match:
                    target_stage = int(stage_match.group(1))
                    for script in dialogue_scripts:
                        if isinstance(script, dict) and str(script.get("stage")) == str(
                            target_stage
                        ):
                            user_dialogue = script.get("user_dialogue", {})
                            if isinstance(user_dialogue, dict):
                                user_persona = user_dialogue.get("user_persona")
                                logger.info(
                                    f"Found user_persona for stage {target_stage}"
                                )
                                break
            if user_persona:
                raw_data["user_persona"] = user_persona
            else:
                logger.warning(
                    "user_persona not found in dialogue_scripts, using empty string"
                )
                raw_data["user_persona"] = ""
            raw_data["current_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Stage 3: Decision
            self.decision_stage.execute(raw_data, global_context, self.llm_client)

            for k, v in raw_data.items():
                logger.info(f"[{k}] [{v}]")
            return {
                "final_response": raw_data["final_response"],
                "global_context": global_context,
            }
        except Exception as e:
            logger.exception(f"UserSimulator v2.0 execution failed: {e}")
            raise e
