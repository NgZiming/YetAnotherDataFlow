import json
import logging

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type
from typing_extensions import TypedDict

from pydantic import BaseModel, Field

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
    output_type: Optional[Type[BaseModel]] = None


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


class StepResponse(TypedDict):
    """Standard response from a single UserStep."""

    raw_text: Optional[str]
    json_resp: Any


# --- Context Models (Pydantic) ---


class AgentContext(BaseModel):
    """
    Tracks the agent's ongoing state and reasoning patterns.
    """

    key_actions: List[str] = Field(
        default_factory=list, description="Key actions taken by the agent"
    )
    key_findings: List[str] = Field(
        default_factory=list, description="Important findings discovered by the agent"
    )
    reasoning_pattern: str = Field(
        ..., description="The current reasoning pattern (e.g., initial_exploration)"
    )
    summary: str = Field(..., description="Summary of the agent's current state")


class DialogueContext(BaseModel):
    """
    Captures the user's intent and emotional context for dialogue generation.
    """

    user_intent: str = Field(..., description="The user's primary intent")
    emotional_tone: str = Field(
        default="neutral", description="Emotional tone (e.g., curious, frustrated)"
    )
    key_questions: List[str] = Field(
        default_factory=list, description="Key questions the user is asking"
    )
    implicit_needs: List[str] = Field(
        default_factory=list, description="Implicit needs not explicitly stated"
    )
    summary: str = Field(..., description="Summary of the dialogue context")
    has_history: bool = Field(
        default=False, description="Whether there is dialogue history"
    )


# --- Understanding Stage Models (Pydantic) ---


class MilestoneInfo(BaseModel):
    """单个里程碑的状态信息"""

    milestone_id: str = Field(..., description="里程碑 ID 或名称")
    status: str = Field(
        ...,
        description="完成状态",
        pattern="^(completed|in_progress|not_started|blocked)$",
    )
    completion_percentage: int = Field(
        ..., ge=0, le=100, description="完成百分比 (0-100)"
    )
    reasoning: str = Field(..., description="判断依据")
    blocking_issues: List[str] = Field(default_factory=list, description="阻碍问题列表")


class MilestoneStatus(BaseModel):
    """MilestoneMatcher 的输出：里程碑完成状态"""

    milestones: List[MilestoneInfo] = Field(..., description="每个里程碑的详细状态")
    overall_progress: int = Field(..., ge=0, le=100, description="整体进度 (0-100)")
    summary: str = Field(..., description="综合总结")


class ProgressAssessment(BaseModel):
    """ProgressEvaluator 的输出：进度评估报告"""

    overall_status: str = Field(
        ...,
        description="整体状态",
        pattern="^(on_track|at_risk|behind|completed)$",
    )
    quality_assessment: str = Field(
        ...,
        description="质量评估",
        pattern="^(high|medium|low)$",
    )
    bottlenecks: List[str] = Field(
        default_factory=list, description="阻碍进展的关键问题"
    )
    risks: List[str] = Field(default_factory=list, description="风险因素列表")
    recommendations: List[str] = Field(default_factory=list, description="建议列表")
    detailed_assessment: str = Field(
        ..., min_length=100, max_length=1000, description="详细的评估报告 (300-500 字)"
    )


class TaskStatePydantic(BaseModel):
    """TaskSynthesizer 的输出：结构化任务状态"""

    current_milestone: str = Field(..., description="当前正在处理的里程碑")
    is_completed: bool = Field(..., description="任务是否已完成")
    completion_reasoning: str = Field(..., description="判断是否完成的依据")
    missing_requirements: List[str] = Field(
        default_factory=list, description="未完成的要求列表"
    )
    next_objective: str = Field(..., description="下一步最优先的目标")
    reasoning: str = Field(
        ..., min_length=50, max_length=500, description="详细的推理过程 (200-300 字)"
    )


# --- Decision Stage Models (Pydantic) ---


class DialogueStrategyPydantic(BaseModel):
    """Strategizer 的输出：对话策略"""

    strategy_type: str = Field(
        ...,
        description="策略类型（必须严格匹配）",
        pattern="^(提问|提供反馈|要求澄清|表达不满|表示满意|催促|其他)$",
        strip_whitespace=True,
    )
    strategy_rationale: str = Field(..., description="选择该策略的原因")
    dialogue_goal: str = Field(..., description="本次对话的目标")
    suggested_approach: str = Field(..., description="建议的对话方式")
    strategy_details: str = Field(
        ..., min_length=50, max_length=300, description="详细的策略描述"
    )


class PersonaContextPydantic(BaseModel):
    """PersonaAdapter 的输出：人设适配结果"""

    persona_traits: List[str] = Field(default_factory=list, description="用户特征列表")
    tone: str = Field(
        ...,
        description="语气（必须严格匹配）",
        pattern="^(正式|随意|友好|严肃|幽默|其他)$",
        strip_whitespace=True,
    )
    language_style: str = Field(
        ...,
        description="语言风格（必须严格匹配）",
        pattern="^(简洁|详细|技术性|通俗化)$",
        strip_whitespace=True,
    )
    response_length: str = Field(
        ...,
        description="响应长度（必须严格匹配）",
        pattern="^(short|medium|long)$",
        strip_whitespace=True,
    )
    persona_context: str = Field(
        ..., min_length=50, max_length=300, description="人设适配后的对话风格描述"
    )


class FinalResponsePydantic(BaseModel):
    """ResponseGenerator 的输出：最终用户反馈"""

    judgment: str = Field(
        ...,
        description="任务状态判断（必须严格匹配）",
        pattern="^(completed|in_progress|aborted)$",
        strip_whitespace=True,
    )
    feedback: str = Field(
        ..., min_length=50, max_length=500, description="具体的用户反馈内容"
    )
    reasoning: str = Field(..., min_length=20, max_length=200, description="判断依据")


class SimulationResult(TypedDict):
    final_response: str
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
        available_keys = set(data_pool.keys())

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
        prompt = self.prompt_template.format(**input_vars)

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

        json_data = json.loads(cleaned_text.strip())

        # 如果定义了 output_type，进行 Pydantic 验证
        if self.schema.output_type:
            validated_model = self.schema.output_type.model_validate(json_data)
            return {"json_resp": validated_model.model_dump(), "raw_text": text}

        return {"json_resp": json_data, "raw_text": text}
