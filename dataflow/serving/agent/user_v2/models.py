from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

# =========================================================================
# UserSimulator v2.0 Core Models
# These models define the cognitive contract across Understanding, 
# Perception, and Decision stages.
# =========================================================================

# --- Perception Models ---

class FileEvidence(BaseModel):
    """ Represents a piece of factual evidence extracted from a file. """
    path: str = Field(..., description="The absolute or relative path of the file")
    fact: str = Field(..., description="The specific fact or finding extracted from the file")
    evidence_snippet: str = Field(..., description="The actual text snippet from the file as proof")
    relevance: str = Field(..., description="Why this is relevant to the user's current goal")

class FileContext(BaseModel):
    """ The collective factual state of the workspace. """
    evidences: List[FileEvidence] = Field(default_factory=list)
    summary: str = Field(..., description="A concise synthesis of all relevant file evidence")

class AgentBehavior(BaseModel):
    """ A structured record of a single agent turn's behavior. """
    action: str = Field(..., description="What the agent actually did (e.g., 'searched for X')")
    finding: str = Field(..., description="What the agent claimed to find")
    is_incremental: bool = Field(..., description="Whether this turn provided new information")

class AgentContext(BaseModel):
    """ The structured analysis of the agent's trajectory. """
    behavior_sequence: List[AgentBehavior] = Field(default_factory=list)
    reasoning_pattern: str = Field(..., description="Current pattern: initial_exploration | gap_filling | hypothesis_testing | course_correction")
    is_looping: bool = Field(..., description="Strict boolean: is the agent stuck in a behavioral loop?")
    loop_analysis: Optional[str] = Field(None, description="Analysis of the repetitive pattern if looping")
    overall_summary: str = Field(..., description="Synthesis of agent's progress and current state")

class DialogueContext(BaseModel):
    """ The subjective state of the user's intent and emotion. """
    user_intent: str = Field(..., description="The current primary goal of the user")
    emotional_tone: str = Field(..., description="Current tone: satisfied | dissatisfied | confused | urgent | neutral")
    key_questions: List[str] = Field(default_factory=list)
    implicit_needs: List[str] = Field(default_factory=list)
    has_history: bool = Field(..., description="Whether this is a subsequent turn")
    summary: str = Field(..., description="Summary of the dialogue evolution")

# --- Understanding Models ---

class MilestoneState(BaseModel):
    """ State of a single milestone. """
    milestone_id: str = Field(...)
    status: str = Field(..., pattern="^(completed|in_progress|not_started|blocked)$")
    completion_percentage: int = Field(..., ge=0, le=100)
    evidence_ref: List[str] = Field(default_factory=list, description="Refs to FileEvidence paths that prove completion")
    reasoning: str = Field(...)

class MilestoneStatus(BaseModel):
    """ Wrapper for a list of milestone states to comply with Pydantic BaseModel requirement. """
    milestones: List[MilestoneState] = Field(..., description="Detailed status of each milestone")

class TaskState(BaseModel):
    """ The absolute truth of the task progress. """
    current_milestone: str = Field(...)
    is_completed: bool = Field(...)
    final_status: str = Field(..., pattern="^(CONTINUE|FINISHED|ABORTED)$")
    emotional_tone: str = Field(..., pattern="^(satisfied|dissatisfied|confused|urgent|neutral)$")
    has_history: bool = Field(...)
    next_objective: str = Field(..., description="The next concrete step to move the task forward")
    reasoning: str = Field(..., description="Detailed logic for the current state")

# --- Decision Models ---

class DialogueStrategy(BaseModel):
    """ The strategic direction for the next response. """
    strategy_type: str = Field(..., pattern="^(提问|提供反馈|要求澄清|表达不满|表示满意|催促|其他)$")
    goal: str = Field(..., description="What this specific response aims to achieve")
    approach: str = Field(..., description="The suggested way to convey the message")

class PersonaStyle(BaseModel):
    """ The linguistic constraints for the current persona. """
    traits: List[str] = Field(default_factory=list)
    tone: str = Field(...)
    style_guide: str = Field(..., description="Specific Do's and Don'ts for this persona's language")
    length_hint: str = Field(..., description="short | medium | long")

class FinalResponse(BaseModel):
    """ The final output delivered to the Agent. """
    judgment: str = Field(..., pattern="^(completed|in_progress|aborted)$")
    feedback: str = Field(..., description="The natural language response")
    reasoning: str = Field(..., description="Internal logic for the judgment and feedback")
