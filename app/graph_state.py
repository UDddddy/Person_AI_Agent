"""LangGraph AgentState 定义（阶段 3 · V3）。

Agent 的全局状态由 TypedDict 描述。messages 字段使用 LangGraph 的
add_messages reducer：每次节点返回新消息时，reducer 负责把新消息追加
到已有历史中（带 id 的 AIMessage 按 id 去重合并），保证状态在节点间
流转时历史消息持续累积，不会丢失。
"""

from typing import TypedDict, Annotated

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Agent 全局状态。

    字段说明：
    - messages：完整的对话消息流（system / user / assistant / tool），
      由 add_messages reducer 自动追加与合并。
    - session_id：当前会话标识，用于 Checkpoint 区分不同场对话。
    """

    messages: Annotated[list, add_messages]
    session_id: str
