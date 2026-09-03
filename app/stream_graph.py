"""LangGraph 流式 Agent（阶段 3 · SSE 流式）。

【本文件为新增文件，由指导侧写入，用于阶段 3「SSE 流式输出」落地】
整体结构与 app/graph_agent.py 同构，差异在两点：
1. LLM 改用 langchain 的 ChatOpenAI（指向 DeepSeek），
   这样 LangGraph 才能通过 callbacks 捕获"逐 token"增量（stream_mode="messages"）。
2. 提供 stream_graph_events 生成器：把图的流式产出转成 (事件类型, 数据) 序列，
   供 FastAPI 的 StreamingResponse 包装成 SSE 推送。

节点逻辑（agent / tools / should_continue）与 graph_agent.py 相同，
只是 agent 节点内部换成了 ChatOpenAI + bind_tools。
"""

import logging
from pathlib import Path

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from config import setting
from graph_state import AgentState
from tools.executor import execute_tool
from tools.registry import TOOL_SCHEMA

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are a helpful assistant."

# Checkpoint 数据库与 graph_agent.py 保持一致，放 app/ 包内
CHECKPOINT_PATH = Path(__file__).resolve().parent / "checkpoints.sqlite"


def get_checkpointer():
    """返回 SqliteSaver 上下文管理器，用于会话持久化（与 graph_agent 一致）。"""
    return SqliteSaver.from_conn_string(str(CHECKPOINT_PATH))


# ---------------------------------------------------------------------------
# 流式 LLM（新写入）
# ---------------------------------------------------------------------------
# ChatOpenAI 是 langchain 对 OpenAI 兼容接口的封装，base_url 指向 DeepSeek。
# bind_tools 把工具 schema 绑到模型上，模型可动态生成 tool_calls。
llm = ChatOpenAI(
    api_key=setting.llm_api_key,
    base_url=setting.llm_base_url,
    model=setting.llm_model,
    temperature=0,
)
llm_with_tools = llm.bind_tools(TOOL_SCHEMA)


# ---------------------------------------------------------------------------
# 图节点（与 graph_agent.py 同构）
# ---------------------------------------------------------------------------

def agent_node(state: AgentState) -> dict:
    """Agent 节点：把消息流交给 ChatOpenAI，模型决定直接回答还是调工具。

    这里用 invoke 即可——LangGraph 的 stream_mode="messages"
    会通过 callbacks 自动捕获模型内部的逐 token 增量。
    """
    response = llm_with_tools.invoke(state["messages"])
    logger.info("agent 节点产出：%s", response)
    return {"messages": [response]}


def tool_node(state: AgentState) -> dict:
    """工具节点：执行最后一条 AIMessage 请求的所有工具调用，结果包装成 ToolMessage。"""
    last = state["messages"][-1]
    tool_messages = []
    for tc in last.tool_calls:
        logger.info("执行工具 %s，参数 %s", tc["name"], tc["args"])
        result = execute_tool(tc["name"], tc["args"])
        tool_messages.append(
            ToolMessage(content=str(result), tool_call_id=tc["id"], name=tc["name"])
        )
    return {"messages": tool_messages}


def should_continue(state: AgentState) -> str:
    """条件边：最后一条是带 tool_calls 的 AIMessage 就继续走 tools，否则结束。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


# ---------------------------------------------------------------------------
# 图构建
# ---------------------------------------------------------------------------

def build_stream_graph(checkpointer=None):
    """构建并编译流式 LangGraph。checkpointer 传入后支持按 thread_id 持久化会话。"""
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)


def _prepare_inputs(compiled, config, user_message: str, checkpointer) -> list:
    """按会话是否已有历史，决定是否注入 system prompt（与 run_graph_agent 一致）。

    checkpointer 为 None 时无法 get_state，直接注入 system prompt；
    有 checkpointer 时才查询会话历史，避免重复注入。
    """
    inputs = []
    if checkpointer is not None:
        current = compiled.get_state(config)
        if not current.values.get("messages"):
            inputs.append(SystemMessage(content=SYSTEM_PROMPT))
    else:
        inputs.append(SystemMessage(content=SYSTEM_PROMPT))
    inputs.append(HumanMessage(content=user_message))
    return inputs


# ---------------------------------------------------------------------------
# 流式生成器（新写入 · 本文件核心）
# ---------------------------------------------------------------------------

def stream_graph_events(user_message: str, session_id: str = "default_session",
                        checkpointer=None):
    """生成器：把图的流式产出转成 (event_type, data) 序列，供 SSE 推送。

    产出的事件类型：
    - ("token", 文本片段)    ：LLM 正在生成的文本增量（打字机效果）
    - ("tool_call", 工具名)  ：模型发起了某次工具调用（可推给前端展示）
    - ("done", 无)           ：本次流式结束

    使用 stream_mode="messages"：LangGraph 会通过 callbacks 捕获模型内部
    的逐 token 增量（AIMessageChunk），产出 (message_chunk, metadata) 元组。
    """
    compiled = build_stream_graph(checkpointer=checkpointer) if checkpointer else build_stream_graph()
    config = {"configurable": {"thread_id": session_id}}
    inputs = _prepare_inputs(compiled, config, user_message, checkpointer)

    for msg_chunk, _metadata in compiled.stream(
        {"messages": inputs, "session_id": session_id},
        config=config,
        stream_mode="messages",
    ):
        # msg_chunk.content 是文本增量（可能是空串）
        content = getattr(msg_chunk, "content", None)
        if content:
            yield ("token", content)

        # msg_chunk.tool_call_chunks 是工具调用参数的增量片段
        tool_chunks = getattr(msg_chunk, "tool_call_chunks", None)
        if tool_chunks:
            # 只在"工具名刚出现"时推一次"正在调用"，避免每个参数片段都推
            name = next((c.get("name") for c in tool_chunks if c.get("name")), None)
            if name:
                yield ("tool_call", name)

    yield ("done", None)
