"""阶段6 · 集成层：树形存储 entry ↔ LangChain Message 互转。

session_store.py 是纯存储层（不依赖 langchain），
本模块负责把 LangChain 的 HumanMessage/AIMessage/ToolMessage/SystemMessage
和树形存储的 entry dict 互相转换，并提供批量持久化。

- entry_to_message：entry → LangChain 消息（fork 标记不发给 LLM）
- chain_to_messages：整条链 → LangChain 消息列表（图入口用）
- append_message_entry：单条 LangChain 消息 → append_entry
- persist_messages：一批消息顺序追加，parent 链自动衔接
"""

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.session_store import append_entry, get_leaf


def entry_to_message(entry):
    """单个 entry dict → LangChain Message。

    - compaction 摘要转成 SystemMessage（告诉 LLM 这是历史摘要）
    - fork 标记返回 None（不发给 LLM，只是分支元数据）
    """
    etype = entry["type"]
    content = entry.get("content") or ""

    if etype == "user":
        return HumanMessage(content=content)
    if etype == "assistant":
        return AIMessage(
            content=content,
            tool_calls=entry.get("tool_calls") or [],
        )
    if etype == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=entry.get("tool_call_id") or "",
        )
    if etype == "system":
        return SystemMessage(content=content)
    if etype == "compaction":
        return SystemMessage(content=f"[历史摘要] {content}")
    # fork 等元数据节点不发给 LLM
    return None


def chain_to_messages(chain):
    """entry 链 → LangChain 消息列表（过滤掉 fork 等 None）。"""
    messages = []
    for entry in chain:
        msg = entry_to_message(entry)
        if msg is not None:
            messages.append(msg)
    return messages


def append_message_entry(session_id, parent_id, msg):
    """单条 LangChain 消息 → append_entry，返回新 entry。"""
    if isinstance(msg, HumanMessage):
        return append_entry(session_id, parent_id, "user", msg.content)
    if isinstance(msg, AIMessage):
        return append_entry(
            session_id,
            parent_id,
            "assistant",
            msg.content or "",
            tool_calls=msg.tool_calls if msg.tool_calls else None,
        )
    if isinstance(msg, ToolMessage):
        return append_entry(
            session_id,
            parent_id,
            "tool",
            str(msg.content),
            tool_call_id=msg.tool_call_id,
        )
    if isinstance(msg, SystemMessage):
        return append_entry(session_id, parent_id, "system", msg.content)
    # 未知类型保守当 user 文本存
    return append_entry(session_id, parent_id, "user", str(getattr(msg, "content", msg)))


def persist_messages(session_id, messages):
    """把一批 LangChain 消息顺序追加到树形存储，parent 链自动衔接。

    以当前叶子作为第一条的 parent；之后每条以上一条新 entry 为 parent。
    返回新追加的 entry 列表。
    """
    leaf = get_leaf(session_id)
    parent_id = leaf["id"] if leaf else None
    entries = []
    for msg in messages:
        entry = append_message_entry(session_id, parent_id, msg)
        entries.append(entry)
        parent_id = entry["id"]
    return entries
