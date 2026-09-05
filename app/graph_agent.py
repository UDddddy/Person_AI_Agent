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
import time
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
from app.config import setting
from app.event_bus import EventBus, EVENT_LLM_RESULT
from tools.pipeline import ToolPipeline
from tools.registry import TOOL_SCHEMA
from app.composition_root import create_pipeline
from app.session_store import init_store, build_chain, compact_session
from app.session_adapter import chain_to_messages, persist_messages
from app.compaction import should_compact
from app.providers import get_provider

logger = logging.getLogger(__name__)
SYSTEM_PROMPT = "You are a helpful assistant."

# 阶段7：通过 ProviderFactory 创建 LLM 实例，业务层不直接碰 openai client
provider = get_provider()

# 阶段9：LLM 层事件总线（与工具层 bus 独立，MetricsCollector 可同时订阅）
llm_bus = EventBus()

# 阶段6：上下文自动压缩阈值（token 估算值）。学习项目设小便于演示，生产环境按模型窗口设。
COMPACT_THRESHOLD = 2000

# Checkpoint 数据库固定放在 app/ 包内，避免启动目录不同导致建错位置
CHECKPOINT_PATH = Path(__file__).resolve().parent / "checkpoints.sqlite"


def get_checkpointer():
    """返回一个 SqliteSaver 上下文管理器（with 语句使用），用于会话持久化。"""
    return SqliteSaver.from_conn_string(str(CHECKPOINT_PATH))


# ---------------------------------------------------------------------------
# LLM 消息格式互转（LangChain messages <-> OpenAI Chat Completions 格式）
# ---------------------------------------------------------------------------

def sanitize_tool_pairs(messages):
    """清洗消息序列，丢弃"孤儿 ToolMessage"。

    OpenAI 协议要求：每条 role=tool 消息，前面必须紧邻一条带对应 id 的
    assistant.tool_calls。并发/重复请求可能让存储链里残留找不到配对的
    ToolMessage（其 AIMessage(tool_calls) 丢失），直接发给 LLM 会 400。
    这里按 tool_call_id 配对，无法配对的 ToolMessage 一律丢弃。

    返回清洗后的新列表（不改原列表）。
    """
    kept = []
    # 最近一条 AIMessage 声明、且尚未被 ToolMessage 回应的 tool_call id 集合
    pending_ids = set()
    for m in messages:
        if isinstance(m, AIMessage):
            kept.append(m)
            if m.tool_calls:
                pending_ids = {tc.get("id") for tc in m.tool_calls if tc.get("id")}
            else:
                # 普通文本回答意味着上一轮工具调用周期已结束
                pending_ids = set()
        elif isinstance(m, ToolMessage):
            # 只有能在"待回应集合"里找到配对的 ToolMessage 才保留
            if m.tool_call_id in pending_ids:
                kept.append(m)
                pending_ids.discard(m.tool_call_id)
            else:
                logger.warning(
                    "丢弃孤儿 ToolMessage(tool_call_id=%s)：找不到配对的 assistant.tool_calls",
                    m.tool_call_id,
                )
        else:
            # user/system 消息不影响配对关系，直接保留
            kept.append(m)
    return kept


def to_openai_messages(messages):
    """把 LangChain 消息列表转成 OpenAI Chat Completions 需要的格式。

    关键点：LangChain 的 AIMessage.tool_calls 是
    [{"name", "args"(dict), "id", "type"}]，而 OpenAI 要求
    {"role":"assistant","tool_calls":[{"id","type","function":{"name","arguments"(json串)}}]}，
    两边的结构差异在这里统一转换。发送前先 sanitize 清洗孤儿 ToolMessage。
    """
    messages = sanitize_tool_pairs(messages)
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
    """调用 LLM，返回 LangChain AIMessage（可能带 tool_calls）。

    阶段7：通过 provider 抽象层调用，不直接碰 openai client。
    阶段9：统计延迟并 emit LLM_RESULT 事件（带 usage），供 MetricsCollector 收集。
    """
    t0 = time.perf_counter()
    resp = provider.chat(to_openai_messages(messages), tools=TOOL_SCHEMA)
    latency = time.perf_counter() - t0

    # 阶段9：上报 LLM 调用指标
    llm_bus.emit(EVENT_LLM_RESULT, {
        "model": resp.model,
        "latency": latency,
        "usage": resp.usage,
        "has_tool_calls": bool(resp.tool_calls),
    })

    if resp.tool_calls:
        return AIMessage(content=resp.content, tool_calls=resp.tool_calls)
    return AIMessage(content=resp.content)


# ---------------------------------------------------------------------------
# 图节点
# ---------------------------------------------------------------------------

def agent_node(state: AgentState) -> dict:
    """Agent 节点：LLM 决策。返回的新 AIMessage 由 add_messages 追加到历史。

    持久化统一在 run_graph_agent 的 invoke 结束后一次性完成，
    节点内不再分散写库（避免并发请求 get_leaf 取错 parent 导致链错乱）。
    """
    try:
        ai_msg = call_llm(state["messages"])
    except Exception as e:
        # 溢出恢复：上下文超长时，保留 system + 最近4条消息，压缩后重试一次
        err = str(e).lower()
        if "context_length" in err or "maximum context" in err or "context window" in err:
            logger.warning("LLM 上下文溢出，压缩后重试一次: %s", e)
            msgs = state["messages"]
            system_msgs = [m for m in msgs if isinstance(m, SystemMessage)][:1]
            recent = msgs[-4:]
            ai_msg = call_llm(system_msgs + recent)
        else:
            raise
    logger.info("agent 节点产出：%s", ai_msg)
    return {"messages": [ai_msg]}


def make_tool_node(pipeline):
    """创建一个工具节点：执行指定工具。"""
    def tool_node(state: AgentState) -> dict:
        """工具节点：执行最后一条 AIMessage 请求的所有工具调用。

        复用既有 execute_tool（内部走 BaseTool.execute 的统一异常兜底），
        把执行结果包装成 ToolMessage 返回给 LLM 上下文。
        持久化统一在 run_graph_agent 结束后一次性完成，节点内不写库。
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

def build_graph(checkpointer=None,pipeline=None):
    """构建并编译 LangGraph。

    checkpointer 传入后（如 SqliteSaver），图支持按 thread_id 持久化会话。
    """
    if pipeline is None:
        pipeline = create_pipeline()
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", make_tool_node(pipeline))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)




def run_graph_agent_detailed(
    user_message: str,
    session_id: str = "default_session",
    checkpointer=None,
) -> dict:
    """对外统一入口（详细版）：以 LangGraph 方式处理一条用户消息。

    返回 {"reply": 最终回答, "tool_trace": 工具执行轨迹列表}。
    tool_trace 每项：{"name", "args", "result", "latency", "blocked"}，
    按调用顺序排列（pipeline.execute 天然保序），供前端渲染"工具调用时间线"。

    阶段6：树形存储作为会话主存储——
    - 入口从 build_chain 重建历史，转 LangChain messages 注入图；
    - 手动 /compact 命令触发压缩；超 COMPACT_THRESHOLD 自动压缩；
    - 持久化在图运行结束后一次性完成。
    checkpointer 保留可传入做图状态，但历史不再依赖它。
    """
    init_store()

    # 手动 /compact：/compact 后面可跟自定义摘要文本
    stripped = user_message.strip()
    if stripped.startswith("/compact"):
        summary = stripped[len("/compact"):].strip() or "此前对话的历史摘要"
        compact_session(session_id, COMPACT_THRESHOLD, summary, force=True)
        logger.info("会话 %s 手动压缩完成", session_id)
        return {"reply": "已压缩上下文", "tool_trace": []}

    # 从树形存储重建历史链
    chain = build_chain(session_id)

    # 阈值自动压缩（学习项目用固定摘要，生产环境应调 LLM 生成摘要）
    if should_compact(chain, COMPACT_THRESHOLD):
        auto_summary = f"[自动压缩] 此前共有 {len(chain)} 条消息，以下为压缩后的对话"
        compact_session(session_id, COMPACT_THRESHOLD, auto_summary)
        chain = build_chain(session_id)
        logger.info("会话 %s 超阈值自动压缩，压缩后 %d 条", session_id, len(chain))

    history = chain_to_messages(chain)

    # 组装图输入：首次会话注入 system prompt + 历史 + 新用户消息
    # system prompt 也要存入树形存储，否则后续轮次重建历史会丢失它
    inputs = []
    new_messages = []
    if not chain:
        sys_msg = SystemMessage(content=SYSTEM_PROMPT)
        inputs.append(sys_msg)
        new_messages.append(sys_msg)
    inputs.extend(history)
    human_msg = HumanMessage(content=user_message)
    inputs.append(human_msg)
    new_messages.append(human_msg)

    # 新消息写入树形存储（节点产出时 parent 链才能衔接）
    persist_messages(session_id, new_messages)
    input_len = len(inputs)  # 记录入口消息数，invoke 后据此切出本次新增部分

    # 【新增】pipeline 在此创建并显式传入图，结束后从中取结构化工具轨迹
    pipeline = create_pipeline()
    compiled = build_graph(checkpointer=checkpointer, pipeline=pipeline)
    config = {"configurable": {"thread_id": session_id}}

    result = compiled.invoke(
        {"messages": inputs, "session_id": session_id},
        config=config,
    )

    # 一次性持久化本次图运行新产生的消息（ai/tool/ai...）。
    # 只在一处批量写库、按结果顺序追加，避免节点内分散 get_leaf 在并发下串错 parent。
    produced = result["messages"][input_len:]
    if produced:
        persist_messages(session_id, produced)

    reply = "未获得回答"
    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage):
            reply = m.content or ""
            break

    return {"reply": reply, "tool_trace": list(pipeline.trace)}


def run_graph_agent(
    user_message: str,
    session_id: str = "default_session",
    checkpointer=None,
) -> str:
    """兼容包装：只返回最终回答文本（详细结果见 run_graph_agent_detailed）。"""
    return run_graph_agent_detailed(user_message, session_id, checkpointer)["reply"]
