import json
import logging
import asyncio

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
    ) -> PerceptionResult:
        logger.info("Entering Perception Stage...")

        # Local pool for this stage, initialized with input data
        local_pool = data_pool.copy()
        raw_results: dict[str, StepResponse] = {}

        # FileSensor: process each file separately with parallel LLM calls
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
            local_pool["file_context"] = file_context

            # Record raw response for FileSensor (符合 StepResponse 规范)
            # StepResponse 允许动态字段 (output_key 对应的字段)
            raw_results["FileSensor"] = {
                "raw_text": json.dumps(file_context, ensure_ascii=False)
            }
        else:
            local_pool["file_context"] = {}
            raw_results["FileSensor"] = {"raw_text": "{}"}

        # Continue with AgentSensor and DialogueSensor
        for step in self.steps[1:]:  # Skip FileSensor (already handled)
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
                "file_context": local_pool.get("file_context", {}),
                "agent_context": local_pool.get("agent_context", ""),
                "dialogue_context": local_pool.get("dialogue_context", ""),
            },
            "raw_responses": raw_results,
        }
