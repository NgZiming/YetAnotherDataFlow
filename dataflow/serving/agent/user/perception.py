import json
import logging
import asyncio

from typing import Any, Dict, Optional
from dataflow.core.agentic import (
    LLMClientABC,
    StepSchema,
    UserStage,
    UserStep,
)
from dataflow.core.agentic.user import AgentContext, DialogueContext

logger = logging.getLogger(__name__)


class PerceptionStage(UserStage):
    """
    Perception Stage: Compresses raw data into structured context.
    Now uses a Step-based registry for automatic data flow.
    Special handling for FileSensor: processes each file separately with parallel LLM calls.
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
                    output_type=AgentContext,
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="DialogueSensor",
                prompt_template=prompts["dialogue_sensor"],
                schema=StepSchema(
                    input_keys=["feedbacks", "agent_context", "file_context"],
                    output_key="dialogue_context",
                    output_type=DialogueContext,
                ),
                llm_config=self.llm_config,
            ),
        ]

    async def _summarize_file(
        self,
        file_path: str,
        file_content: str,
        prompt_template: str,
        llm_client: LLMClientABC,
    ) -> tuple[str, str]:
        """
        Summarize a single file.

        Args:
            file_path: Path of the file
            file_content: Content of the file
            prompt_template: Prompt template with {file_path} and {file_content} placeholders
            llm_client: LLM client

        Returns:
            Tuple of (file_path, summary)
        """
        try:
            # Fill prompt with single file content
            prompt = prompt_template.format(
                file_path=file_path, file_content=file_content
            )

            # Call LLM
            summary = await llm_client.generate(prompt, config=self.llm_config)

            return (file_path, summary)
        except Exception as e:
            logger.error(f"Failed to summarize file {file_path}: {e}")
            return (file_path, f"[Error summarizing file: {str(e)}]")

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ):
        logger.info("Entering Perception Stage...")

        # ========== 预先检查所有步骤的输入依赖 ==========
        self._check_step_dependencies(self.steps, data_pool, "PerceptionStage")

        # ========== FileSensor: process each file separately with parallel LLM calls ==========
        file_sensor_step = self.steps[0]
        file_contents: Dict[str, str] = data_pool.get("file_contents", {})

        if file_contents:
            logger.info(f"Processing {len(file_contents)} files in parallel...")

            # Create tasks for parallel execution
            tasks = [
                self._summarize_file(
                    file_path=path,
                    file_content=content,
                    prompt_template=file_sensor_step.prompt_template,
                    llm_client=llm_client,
                )
                for path, content in file_contents.items()
            ]

            # Execute all tasks in parallel
            results = await asyncio.gather(*tasks)

            # Convert results to dict: {file_path: summary}
            file_context = dict(results)
            data_pool[file_sensor_step.schema.output_key] = file_context
        else:
            logger.warning(
                "No file_contents provided, FileSensor will produce empty file_context"
            )
            data_pool[file_sensor_step.schema.output_key] = {}

        # ========== 执行剩余步骤 ==========
        for step in self.steps[1:]:  # Skip FileSensor (already handled above)
            # Execute the step
            res = await step.execute(data_pool, global_context, llm_client)
            data_pool[step.schema.output_key] = res["json_resp"]

        for step in self.steps:
            logger.info(f"{data_pool[step.schema.output_key]}")
