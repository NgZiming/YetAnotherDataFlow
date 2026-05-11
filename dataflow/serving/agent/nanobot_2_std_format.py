from typing import List
from dataflow.core.agentic import (
    TrajectoryDict,
    MessageDict,
    ToolCallDict,
    ToolResultDict,
)
from nanobot.agent.hook import AgentHookContext


def convert(c: AgentHookContext) -> TrajectoryDict:
    """
    将 Nanobot 的 AgentHookContext 转换为标准 TrajectoryDict 格式。
    """
    messages: List[MessageDict] = []

    # 使用计数器追踪 round
    # 逻辑：遇到 role == "user" 时，round 增加 1
    current_round = 0

    # AgentHookContext.messages 包含了对话的完整历史。
    for idx, msg in enumerate(c.messages):
        # 获取角色 (role)
        role = msg.get("role", "unknown")

        # 只有非 system 消息才参与 round 计算
        if role == "user":
            current_round += 1

        # 获取内容 (content)
        content = msg.get("content", "")

        # 针对 Assistant 消息，尝试提取思考过程和工具调用
        thought = msg.get("thought", "")

        tool_calls: list[ToolCallDict] = []
        if role == "assistant":
            # 提取工具调用列表
            raw_tool_calls = msg.get("tool_calls", [])
            if raw_tool_calls:
                for tc in raw_tool_calls:
                    tc_type = tc.get("type", "function")
                    tool_calls.append(
                        {
                            "id": tc.get("id", None),
                            "name": tc.get(tc_type, {}).get("name", "unknown"),
                            "arguments": tc.get(tc_type, {}).get("name", "arguments"),
                        }
                    )

        # 针对 Tool 消息，处理工具执行结果
        tool_results: list[ToolResultDict] = []
        tool_call_id = None
        if role == "tool":
            # nanobot 的 tool 消息通常包含对应的 call_id 和 tool_name
            tool_call_id = getattr(msg, "tool_call_id", None)
            tool_name = getattr(msg, "tool_name", "unknown")

            # tool_results 结构: [{"toolCallId": ..., "toolName": ..., "content": ...}]
            if content:
                tool_results.append(
                    {
                        "toolCallId": tool_call_id,
                        "toolName": tool_name,
                        "content": content,
                    }
                )

        # 构建标准消息字典
        msg_dict: MessageDict = {
            "round": 0 if role == "system" else current_round,
            "role": role,
            "content": content,
            "thought": thought,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "id": msg.get("id", f"msg_{idx}"),
            "parentId": None,
            "session_id": None,
        }

        messages.append(msg_dict)

    # 构建最终的轨迹对象
    rtn = TrajectoryDict(
        task_id="",
        final_output=c.final_content or "",
        total_rounds=current_round,
        is_completed=False,
        messages=messages,
        metadata={
            "usage": c.usage,
            "stop_reason": c.stop_reason,
            "error": c.error,
        },
    )
    return rtn
