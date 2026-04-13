"""
Agent-related LLM Serving implementations.

包含与 Agent 相关的 serving 实现：
- CLIOpenClawServing: 通过 OpenClaw CLI 调用 agent
- SDKNanobotServing: 通过 Nanobot SDK 调用 agent
- System Prompt Builder: 构建 OpenClaw system prompt
"""

from .cli_openclaw_serving import CLIOpenClawServing, create_openclaw_serving
from .sdk_nanobot_serving import SDKNanobotServing, create_nanobot_serving
from .system_prompt_builder import (
    build_system_prompt,
    save_system_prompt,
    load_system_prompt,
    get_current_time_string,
)

__all__ = [
    "CLIOpenClawServing",
    "create_openclaw_serving",
    "SDKNanobotServing",
    "create_nanobot_serving",
    "build_system_prompt",
    "save_system_prompt",
    "load_system_prompt",
    "get_current_time_string",
]
