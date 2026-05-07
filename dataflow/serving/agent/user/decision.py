import asyncio
import logging

from typing import Any, Dict, Optional

from dataflow.core.agentic import LLMClientABC, StepSchema, UserStage, UserStep
from dataflow.core.agentic.user import (
    DialogueStrategyPydantic,
    PersonaContextPydantic,
    FinalResponsePydantic,
)

logger = logging.getLogger(__name__)


class DecisionStage(UserStage):
    """
    Decision Stage: Strategy + Persona -> Final Response.
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
                name="Strategizer",
                prompt_template=prompts["strategizer"],
                schema=StepSchema(
                    input_keys=["task_state", "dialogue_scripts"],
                    output_key="dialogue_strategy",
                    output_type=DialogueStrategyPydantic,
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="PersonaAdapter",
                prompt_template=prompts["persona_adapter"],
                schema=StepSchema(
                    input_keys=["task_state", "user_persona"],
                    output_key="persona_context",
                    output_type=PersonaContextPydantic,
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="ResponseGenerator",
                prompt_template=prompts["response_generator"],
                schema=StepSchema(
                    input_keys=[
                        "dialogue_strategy",
                        "persona_context",
                        "task_state",
                        "dialogue_context",
                        "question",
                        "file_context",
                        "milestones",
                    ],
                    output_key="final_response",
                    output_type=FinalResponsePydantic,
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
        logger.info("Entering Decision Stage...")

        # ========== 从 dialogue_scripts 中提取当前 stage 的 user_persona ==========
        # dialogue_scripts 结构：[{"stage": 1, "user_dialogue": {"user_persona": {...}}}, ...]
        dialogue_scripts = data_pool.get("dialogue_scripts", [])
        task_state = data_pool.get("task_state", {})
        user_persona = None

        if dialogue_scripts and task_state:
            # 从 task_state 中获取当前 milestone 的 stage 编号
            # task_state 包含 current_milestone 字段（格式如 "stage_1"）
            current_milestone = task_state.get("current_milestone", "")

            # 尝试从 current_milestone 中提取 stage 编号
            # 格式："stage_1" → target_stage = 1
            import re

            stage_match = re.search(
                r"stage_(\d+)", str(current_milestone), re.IGNORECASE
            )

            if stage_match:
                target_stage = int(stage_match.group(1))
                # 直接遍历 dialogue_scripts 找到 stage 匹配的条目
                for script in dialogue_scripts:
                    if isinstance(script, dict) and script.get("stage") == target_stage:
                        user_dialogue = script.get("user_dialogue", {})
                        if isinstance(user_dialogue, dict):
                            user_persona = user_dialogue.get("user_persona")
                            logger.info(f"Found user_persona for stage {target_stage}")
                            break
        if user_persona:
            data_pool["user_persona"] = user_persona
        else:
            logger.warning(
                "user_persona not found in dialogue_scripts, using empty string"
            )
            data_pool["user_persona"] = ""

        # ========== 预先检查所有步骤的输入依赖 ==========
        self._check_step_dependencies(self.steps, data_pool, "DecisionStage")

        # ========== 执行并行步骤 ==========
        strategy_task = self.steps[0].execute(data_pool, global_context, llm_client)
        persona_task = self.steps[1].execute(data_pool, global_context, llm_client)

        strategy_res, persona_res = await asyncio.gather(strategy_task, persona_task)
        data_pool["dialogue_strategy"] = strategy_res["json_resp"]
        data_pool["persona_context"] = persona_res["json_resp"]

        # ========== 执行最终合成步骤 ==========
        # 现在 dialogue_strategy 和 persona_context 已经在 data_pool 中，可以执行 ResponseGenerator
        response_step = self.steps[2]
        gen_res = await response_step.execute(data_pool, global_context, llm_client)
        data_pool["final_response"] = gen_res["json_resp"]

        logger.info(f"DialogueStrategy: {data_pool['dialogue_strategy']}")
        logger.info(f"PersonaContext: {data_pool['persona_context']}")
        logger.info(f"FinalResponse: {data_pool['final_response']}")

        # 返回平铺结构
        return {
            "dialogue_strategy": data_pool["dialogue_strategy"],
            "persona_context": data_pool["persona_context"],
            "final_response": data_pool["final_response"],
            "raw_responses": {
                "Strategizer": strategy_res,
                "PersonaAdapter": persona_res,
                "ResponseGenerator": gen_res,
            },
        }
