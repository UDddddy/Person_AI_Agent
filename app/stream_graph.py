"""LangGraph 流式 Agent（真·逐 token SSE 流式）。

与非流式 graph_agent.py 的核心差异：
- agent_node 内部调用 provider.stream()（而非 provider.chat()），逐 token 累积，
  同时通过 LangGraph 的 StreamWriter 把每个增量推给外层。
- 外层用 stream_mode="custom" 接收 StreamWriter 的输出，直接转成 SSE 事件。
- 工具调用的 arguments 在流式中是增量字符串，需按 index 累积后再 json.loads。

事件格式（供 FastAPI StreamingResponse 包装成 SSE）：
  ("token", 文本片段)       — LLM 逐字输出
  ("tool_call", 工具名)     — 模型发起工具调用（首次出现工具名时推一次）
  ("tool_result", {name, content}) — 工具执行完成
  ("done", None)            — 本轮结束
"""

import json
import logging
from pathlib import Path

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import StreamWriter

from app.graph_state import AgentState
from app.graph_agent import to_openai_messages
from app.composition_root import create_pipeline
from app.providers import get_provider
from tools.registry import TOOL_SCHEMA

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are a helpful assistant."

# 阶段7：通过 ProviderFactory 创建 LLM 实例
provider = get_provider()

CHECKPOINT_PATH = Path(__file__).resolve().parent / "checkpoints.sqlite"


def get_checkpointer():
    """返回 SqliteSaver 上下文管理器，用于会话持久化。"""
    return SqliteSaver.from_conn_string(str(CHECKPOINT_PATH))


# ---------------------------------------------------------------------------
# 图节点
# ---------------------------------------------------------------------------

def agent_node(state: AgentState, writer: StreamWriter) -> dict:
    """流式 Agent 节点：逐 token 推送，同时累积完整响应。

    - provider.stream() yield 的每个 LLMResponse.content 是文本增量；
    - tool_calls 是增量（arguments 逐字到达），按 index 累积字符串，
      流结束后统一 json.loads 成 dict。
    - StreamWriter 把 token/tool_call 事件实时推给外层。
    """
    full_content = ""
    # index -> {"name": str, "args": str(累积), "id": str, "emitted": bool}
    tc_buf: dict[int, dict] = {}

    for resp in provider.stream(to_openai_messages(state["messages"]), tools=TOOL_SCHEMA):
        # 1. 文本增量
        if resp.content:
            full_content += resp.content
            writer({"type": "token", "content": resp.content})

        # 2. 工具调用增量（arguments 是逐字字符串，按 index 累积）
        for tc in resp.tool_calls:
            idx = tc.get("index", 0)
            if idx not in tc_buf:
                tc_buf[idx] = {"name": "", "args": "", "id": "", "emitted": False}
            buf = tc_buf[idx]
            if tc.get("name"):
                buf["name"] = tc["name"]
            if tc.get("id"):
                buf["id"] = tc["id"]
            if tc.get("args"):
                buf["args"] += tc["args"]
            # 工具名首次确定时推送 tool_call 事件（只推一次，args 暂为空对象）
            if buf["name"] and not buf["emitted"]:
                writer({"type": "tool_call", "content": {"name": buf["name"], "args": {}}})
                buf["emitted"] = True

    # 3. 流结束：把累积的 arguments 字符串解析成 dict，组装最终 tool_calls
    final_tool_calls = []
    for idx in sorted(tc_buf.keys()):
        buf = tc_buf[idx]
        try:
            args = json.loads(buf["args"]) if buf["args"] else {}
        except json.JSONDecodeError:
            args = {"_raw": buf["args"]}
        final_tool_calls.append({
            "name": buf["name"],
            "args": args,
            "id": buf["id"],
            "type": "tool_call",
        })

    ai_msg = AIMessage(content=full_content, tool_calls=final_tool_calls)
    logger.info("流式 agent 节点完成：content_len=%d, tool_calls=%d",
                len(full_content), len(final_tool_calls))
    return {"messages": [ai_msg]}


def make_stream_tool_node(pipeline):
    """创建流式工具节点：执行工具后通过 StreamWriter 推送 tool_result 事件。"""
    def tool_node(state: AgentState, writer: StreamWriter) -> dict:
        last = state["messages"][-1]
        tool_messages = []
        for tc in last.tool_calls:
            logger.info("执行工具 %s，参数 %s", tc["name"], tc["args"])
            result = pipeline.execute(tc["name"], tc["args"])
            writer({"type": "tool_result", "content": {
                "name": tc["name"], "content": str(result),
            }})
            tool_messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"], name=tc["name"])
            )
        return {"messages": tool_messages}
    return tool_node


def should_continue(state: AgentState) -> str:
    """条件边：最后一条是带 tool_calls 的 AIMessage 就继续走 tools，否则结束。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


# ---------------------------------------------------------------------------
# 图构建
# ---------------------------------------------------------------------------

def build_stream_graph(checkpointer=None, pipeline=None):
    """构建并编译流式 LangGraph。"""
    if pipeline is None:
        pipeline = create_pipeline()
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", make_stream_tool_node(pipeline))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)


def _prepare_inputs(compiled, config, user_message: str, checkpointer) -> list:
    """按会话是否已有历史，决定是否注入 system prompt。

    checkpointer 为 None 时无法 get_state，直接注入 system；
    有 checkpointer 时查询图状态，空会话才注入，避免重复。
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
# 流式生成器（SSE 事件源）
# ---------------------------------------------------------------------------

def stream_graph_events(user_message: str, session_id: str = "default_session",
                        checkpointer=None):
    """生成器：逐事件 yield (event_type, data)，供 FastAPI 包装成 SSE。

    使用 stream_mode="custom"：接收 agent_node / tool_node 里 StreamWriter
    写入的 {"type", "content"} dict，直接解包 yield。
    图自然结束后 yield ("done", None)。
    """
    compiled = build_stream_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    inputs = _prepare_inputs(compiled, config, user_message, checkpointer)

    for event in compiled.stream(
        {"messages": inputs, "session_id": session_id},
        config=config,
        stream_mode="custom",
    ):
        # event 就是 writer({"type": ..., "content": ...}) 写入的 dict
        if isinstance(event, dict) and "type" in event:
            yield (event["type"], event.get("content"))

    yield ("done", None)
