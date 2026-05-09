import json
import logging
import json_repair

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


@dataclass
class StepSchema:
    """
    Defines the complete configuration for a UserStep.
    """

    input_keys: List[str]  # Variables required from the data pool to fill the prompt
    output_key: (
        str  # The specific key in the LLM JSON response that represents the result
    )
    prompt_template: str  # The prompt template for this step
    output_type: Optional[Type] = None  # Pydantic model class for validation


class LLMClientABC(ABC):
    """
    Abstract Base Class for LLM clients used by the User Simulator.
    This allows the simulator to be agnostic of the actual transport layer (REST, gRPC, etc.)
    """

    @abstractmethod
    async def generate(
        self, prompt: str, config: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate a text response from the LLM.
        """
        pass


class StepResponse(TypedDict):
    """Standard response from a single UserStep."""

    raw_text: Optional[str]
    json_resp: Any


class SimulationResult(TypedDict):
    final_response: dict[str, str]
    global_context: Dict[str, Any]


class UserSimulatorABC(ABC):
    @abstractmethod
    async def run(
        self,
        raw_data: Dict[str, Any],
        global_context: Optional[Dict[str, Any]] = None,
    ) -> SimulationResult:
        pass


class UserStage(ABC):
    """
    Abstract Base Class for User Stages (Perception, Understanding, Decision).
    Provides common utilities for dependency checking and step execution.
    """

    @abstractmethod
    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ):  # Returns the specific Result TypedDict of the subclass
        pass

    def _check_step_dependencies(
        self,
        steps: List["UserStep"],
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        error_prefix: str,
    ):
        """
        Check if all steps have their required input keys available.

        Args:
            steps: List of UserStep to check
            data_pool: Current data pool
            error_prefix: Prefix for error message (e.g., "PerceptionStage", "UnderstandingStage")

        Returns:
            Error message if dependencies are missing, None if all checks pass
        """
        available_keys = set(list(global_context.keys()) + list(data_pool.keys()))

        for step in steps:
            missing_keys = []
            for key in step.schema.input_keys:
                if key not in available_keys:
                    missing_keys.append(key)

            if missing_keys:
                raise Exception(
                    f"{error_prefix}: Step [{step.name}] requires missing keys: "
                    f"{missing_keys}. Available: {list(available_keys)}"
                )

            # Add this step's output key to available keys for subsequent steps
            available_keys.add(step.schema.output_key)


class UserStep:
    """
    A contract-driven execution unit.
    Automatically fetches inputs and validates outputs based on its StepSchema.
    """

    def __init__(
        self,
        name: str,
        schema: StepSchema,
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.schema = schema
        self.llm_config = llm_config or {}

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ) -> StepResponse:
        """
        Auto-fetch inputs -> Call LLM -> Validate output key.
        """
        # 1. Auto-fetch: Pull required variables from data_pool or global_context
        input_vars = {}
        for key in self.schema.input_keys:
            val = data_pool.get(key) or global_context.get(key, "")
            input_vars[key] = val

        # 2. Template filling
        prompt = self.schema.prompt_template.format(**input_vars)

        # 3. LLM Call
        response_text = await llm_client.generate(prompt, config=self.llm_config)

        # 4. JSON Parsing
        return self._parse_json(response_text)

    def _parse_json(self, text: str) -> StepResponse:
        """Parse JSON and validate against output_type if specified."""
        cleaned_text = text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]

        try:
            # First attempt with standard json.loads
            json_data = json.loads(cleaned_text.strip())
        except json.JSONDecodeError:
            # Fallback to json_repair for malformed JSON
            logger.warning(
                f"Standard JSON parsing failed for step [{self.name}], attempting json_repair. Text: {text[:100]}..."
            )
            json_data = json_repair.repair_json(
                cleaned_text.strip(),
                return_objects=True,
            )

        # 如果定义了 output_type，进行 Pydantic 验证
        if self.schema.output_type:
            validated_model = self.schema.output_type.model_validate(json_data)
            return {"json_resp": validated_model.model_dump(), "raw_text": text}

        return {"json_resp": json_data, "raw_text": text}
