"""基于 LangGraph 的 Agent 编排（阶段 3 · V3）。

把手写 Agent Loop（app/agent.py 的 run_agent）迁移为 StateGraph：

    START → agent ──(有 tool_calls)──> tools ──> agent ── ...
                └──(无 tool_calls)──> END

- agent 节点：把当前消息流交给 LLM，LLM 决定是直接回答还是调用工具。
- tools 节点：执行 agent 请求的工具调用，把结果作为 ToolMessage 放回状态。
- 条件边：根据最后一条 AIMessage 是否带 tool_calls 决定走向。
- Checkpoint：可选，接入后按 thread_id（映射 session_id）保存会话，重启不丢。

复用既有工具体系（tools/base.py + registry.py + executor.py），
LLM 调用沿用 app/llm.py 的 OpenAI-compatible client。
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

from app.graph_state import AgentState
from app.llm import client
from app.config import setting
from tools.pipeline import ToolPipeline
from tools.registry import TOOL_SCHEMA

logger = logging.getLogger(__name__)
pipeline = ToolPipeline()   
SYSTEM_PROMPT = "You are a helpful assistant."

# Checkpoint 数据库固定放在 app/ 包内，避免启动目录不同导致建错位置
CHECKPOINT_PATH = Path(__file__).resolve().parent / "checkpoints.sqlite"


def get_checkpointer():
    """返回一个 SqliteSaver 上下文管理器（with 语句使用），用于会话持久化。"""
    return SqliteSaver.from_conn_string(str(CHECKPOINT_PATH))


# ---------------------------------------------------------------------------
# LLM 消息格式互转（LangChain messages <-> OpenAI Chat Completions 格式）
# ---------------------------------------------------------------------------

def to_openai_messages(messages):
    """把 LangChain 消息列表转成 OpenAI Chat Completions 需要的格式。

    关键点：LangChain 的 AIMessage.tool_calls 是
    [{"name", "args"(dict), "id", "type"}]，而 OpenAI 要求
    {"role":"assistant","tool_calls":[{"id","type","function":{"name","arguments"(json串)}}]}，
    两边的结构差异在这里统一转换。
    """
    out = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            d = {"role": "assistant", "content": m.content or ""}
            if m.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"], ensure_ascii=False),
                        },
                    }
                    for tc in m.tool_calls
                ]
            out.append(d)
        elif isinstance(m, ToolMessage):
            out.append(
                {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
            )
        else:
            # 未知类型保守处理：按普通文本消息处理
            out.append({"role": "user", "content": str(getattr(m, "content", m))})
    return out


def call_llm(messages):
    """调用 LLM，返回 LangChain AIMessage（可能带 tool_calls）。"""
    response = client.chat.completions.create(
        model=setting.llm_model,
        messages=to_openai_messages(messages),
        tools=TOOL_SCHEMA,
    )
    msg = response.choices[0].message
    if msg.tool_calls:
        return AIMessage(
            content=msg.content or "",
            tool_calls=[
                {
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments),
                    "id": tc.id,
                    "type": "tool_call",
                }
                for tc in msg.tool_calls
            ],
        )
    return AIMessage(content=msg.content or "")


# ---------------------------------------------------------------------------
# 图节点
# ---------------------------------------------------------------------------

def agent_node(state: AgentState) -> dict:
    """Agent 节点：LLM 决策。返回的新 AIMessage 由 add_messages 追加到历史。"""
    ai_msg = call_llm(state["messages"])
    logger.info("agent 节点产出：%s", ai_msg)
    return {"messages": [ai_msg]}


def tool_node(state: AgentState) -> dict:
    """工具节点：执行最后一条 AIMessage 请求的所有工具调用。

    复用既有 execute_tool（内部走 BaseTool.execute 的统一异常兜底），
    把执行结果包装成 ToolMessage 返回给 LLM 上下文。
    """
    last = state["messages"][-1]
    tool_messages = []
    for tc in last.tool_calls:
        logger.info("执行工具 %s，参数 %s", tc["name"], tc["args"])
        result = pipeline.execute(tc["name"], tc["args"])
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

def build_graph(checkpointer=None):
    """构建并编译 LangGraph。

    checkpointer 传入后（如 SqliteSaver），图支持按 thread_id 持久化会话。
    """
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


# 默认图：无 checkpointer（进程内状态，随运行结束即失忆）
graph = build_graph()


def run_graph_agent(
    user_message: str,
    session_id: str = "default_session",
    checkpointer=None,
) -> str:
    """对外统一入口：以 LangGraph 方式处理一条用户消息。

    - 首次进入某 session_id 时注入 system prompt；
    - 已存在的会话（Checkpoint 恢复）自动携带历史，只追加当前用户消息；
    - 返回最后一条 AIMessage 的文本作为最终回答。
    """
    compiled = build_graph(checkpointer=checkpointer) if checkpointer else graph
    config = {"configurable": {"thread_id": session_id}}

    inputs = []
    if checkpointer is None:
        # 无 checkpointer：图不保存历史，每次都是全新会话，直接注入 system prompt
        inputs.append(SystemMessage(content=SYSTEM_PROMPT))
    else:
        # 有 checkpointer：通过 get_state 判断该会话是否已有历史，避免重复注入
        current = compiled.get_state(config)
        if not current.values.get("messages"):
            inputs.append(SystemMessage(content=SYSTEM_PROMPT))
    inputs.append(HumanMessage(content=user_message))

    result = compiled.invoke(
        {"messages": inputs, "session_id": session_id},
        config=config,
    )

    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage):
            return m.content or ""
    return "未获得回答"
