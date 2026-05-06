import json
import logging

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


@dataclass
class StepSchema:
    """
    Defines the input/output contract for a UserStep.
    """

    input_keys: List[str]  # Variables required from the data pool to fill the prompt
    output_key: (
        str  # The specific key in the LLM JSON response that represents the result
    )


class ContractViolationError(Exception):
    """Raised when an LLM response does not adhere to the StepSchema."""

    pass


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


# --- Structured Results Definitions ---


class StepResponse(TypedDict, total=False):
    """Standard response from a single UserStep."""

    error: str
    missing_key: Optional[str]
    raw_text: Optional[str]
    # The actual result is usually a dynamic key defined by StepSchema,
    # but this TypedDict handles the 'envelope' and error states.


class PerceptionContext(TypedDict):
    file_context: str
    agent_context: str
    dialogue_context: str


class PerceptionResult(TypedDict):
    context: PerceptionContext
    raw_responses: Dict[str, StepResponse]


class TaskState(TypedDict):
    current_milestone: str
    is_completed: bool
    missing_requirements: List[str]
    next_objective: str
    reasoning: str


class UnderstandingIntermediate(TypedDict):
    milestone_status: str
    progress_assessment: str


class UnderstandingResult(TypedDict):
    task_state: TaskState
    intermediate_understanding: UnderstandingIntermediate
    raw_responses: Dict[str, StepResponse]


class DecisionDetails(TypedDict):
    dialogue_strategy: str
    persona_context: str


class DecisionResult(TypedDict):
    final_response: str
    decision_details: DecisionDetails
    raw_responses: Dict[str, StepResponse]


class SimulationTrajectory(TypedDict):
    perception: PerceptionResult
    understanding: UnderstandingResult
    decision: DecisionResult


class SimulationResult(TypedDict):
    final_response: str
    trajectory: SimulationTrajectory
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
    @abstractmethod
    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ) -> Any:  # Returns the specific Result TypedDict of the subclass
        pass


class UserStep:
    """
    A contract-driven execution unit.
    Automatically fetches inputs and validates outputs based on its StepSchema.
    """

    def __init__(
        self,
        name: str,
        prompt_template: str,
        schema: StepSchema,
        llm_config: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.prompt_template = prompt_template
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
        try:
            prompt = self.prompt_template.format(**input_vars)
        except KeyError as e:
            logger.error(
                f"Step [{self.name}] missing required variable in data pool: {e}"
            )
            raise

        # 3. LLM Call
        response_text = await llm_client.generate(prompt, config=self.llm_config)

        # 4. JSON Parsing
        res_json = self._parse_json(response_text)

        # 5. Contract Validation
        if "error" in res_json:
            return res_json  # Propagate parse errors

        if self.schema.output_key not in res_json:
            logger.error(
                f"Step [{self.name}] contract violation: missing output key '{self.schema.output_key}'. Raw: {response_text}"
            )
            return {
                "error": "CONTRACT_VIOLATION",
                "missing_key": self.schema.output_key,
                "raw_text": response_text,
            }

        return res_json

    def _parse_json(self, text: str) -> Dict[str, Any]:
        try:
            cleaned_text = text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            return json.loads(cleaned_text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Step [{self.name}] failed to parse JSON: {e}")
            return {"error": "JSON_PARSE_FAILED", "raw_text": text}
