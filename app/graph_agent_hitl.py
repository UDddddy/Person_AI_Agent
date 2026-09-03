"""基于 LangGraph 的 Agent 编排（阶段 3 · V3 · HITL 人工审批版）。

与 app/graph_agent.py 的关系：这是它的"带人工审批"版本。
区别只有一处核心——tools 节点会对**危险工具**（run_command）执行人工审批：

    START → agent ──(有 tool_calls)──> tools ──(危险工具 interrupt)──> agent ── ...
                └──(无 tool_calls)──> END

HITL 机制（LangGraph interrupt）：
- 节点内调用 `interrupt(value)` → 图暂停，把 value 抛给外部（进入 __interrupt__）。
- 外部用 `Command(resume=...)` 恢复 → 节点从头重跑，interrupt 返回 resume 值。
- 因此 interrupt **必须**配 checkpointer（中断依赖持久化图状态）。

本文件三个对外可用入口：
- build_graph(checkpointer)：构造 HITL 图（checkpointer 必填）。
- run_hitl(user_message, session_id, ask_human)：两段式驱动，命令行/回调收集人类决策。
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
from langgraph.types import Command, interrupt

from app.config import setting
from app.graph_state import AgentState
from app.llm import client
from tools.executor import execute_tool
from tools.registry import TOOL_SCHEMA

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are a helpful assistant."

# Checkpoint 数据库固定放在 app/ 包内，避免启动目录不同导致建错位置
CHECKPOINT_PATH = Path(__file__).resolve().parent / "checkpoints.sqlite"

# 危险工具集合：这些工具执行前必须人工审批
DANGEROUS_TOOLS = {"run_command"}


def get_checkpointer():
    """返回 SqliteSaver 上下文管理器（需用 with 进入才能拿到 saver 实例）。

    from_conn_string 返回的是 context manager，真正可用的 saver 在
    `with ... as cp:` 里拿到——调用方负责 with 生命周期。
    """
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


def tool_node_with_approval(state: AgentState) -> dict:
    """工具节点：危险工具先 interrupt 审批，批准才执行；安全工具直接执行。

    关键设计：
    - 危险工具（如 run_command）→ interrupt 暂停图，把 {tool, args} 抛给外部；
      外部 resume="approve" 则执行，resume=其他（如 "reject"）则不执行，
      但两种结果都会返回 ToolMessage —— 让 LLM 知道这次调用被处理了。
    - 拒绝不能静默跳过：否则 LLM 会以为工具执行了，逻辑断裂甚至死循环。
    """
    last = state["messages"][-1]
    tool_messages = []
    for tc in last.tool_calls:
        if tc["name"] in DANGEROUS_TOOLS:
            logger.warning("危险工具 %s 待审批，参数 %s", tc["name"], tc["args"])
            # 暂停图，把"待审批内容"抛给外部；恢复后这里返回外部决策
            decision = interrupt({"tool": tc["name"], "args": tc["args"]})
            if decision == "approve":
                result = execute_tool(tc["name"], tc["args"])
                tool_messages.append(
                    ToolMessage(
                        content=str(result),
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
            else:
                # 拒绝：不执行，但回一条"被拒"消息让 LLM 看到
                tool_messages.append(
                    ToolMessage(
                        content=f"用户拒绝了工具 {tc['name']} 的执行（参数：{tc['args']}）",
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
        else:
            logger.info("执行工具 %s，参数 %s", tc["name"], tc["args"])
            result = execute_tool(tc["name"], tc["args"])
            tool_messages.append(
                ToolMessage(
                    content=str(result),
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
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

def build_graph(checkpointer):
    """构建并编译 HITL LangGraph。

    checkpointer **必填**：interrupt 依赖持久化图状态，没有它会直接报错。
    """
    if checkpointer is None:
        raise ValueError("HITL 图必须提供 checkpointer（interrupt 依赖它持久化状态）")

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node_with_approval)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# HITL 两段式驱动（外部怎么喂审批结果）
# ---------------------------------------------------------------------------

def default_ask_human(payload) -> str:
    """默认决策收集：命令行 input()，供 demo 使用。

    真实场景（API/前端）应替换为：把 payload 展示给用户 → 收集用户选择。
    """
    print(f"\n[HITL 审批] 工具 {payload['tool']}，参数 {payload['args']}")
    while True:
        decision = input("是否批准执行？(approve/reject): ").strip().lower()
        if decision in ("approve", "reject"):
            return decision
        print("无效输入，请输入 approve 或 reject")


def run_hitl(
    user_message: str,
    session_id: str = "default_session",
    ask_human=None,
) -> str:
    """两段式 HITL 驱动：运行 → 中断 → 人类决策 → 恢复 → 取最终答案。

    为什么不能"一次 invoke 跑完"：图遇到 interrupt 时 invoke 返回的结果里
    带 `__interrupt__` 键（图暂停），需要人类决策后再用 Command(resume=...)
    恢复，图才继续跑完。危险工具可能有多个 → 用 while 循环处理多次中断。
    """
    if ask_human is None:
        ask_human = default_ask_human

    config = {"configurable": {"thread_id": session_id}}

    # SqliteSaver.from_conn_string 返回上下文管理器，必须 with 进入拿 saver，
    # 整个运行-中断-恢复过程都在这一个连接内完成
    with get_checkpointer() as cp:
        compiled = build_graph(checkpointer=cp)

        # system prompt 注入只留一份：已有历史不注入，全新会话才注入
        inputs = []
        current = compiled.get_state(config)
        if not current.values.get("messages"):
            inputs.append(SystemMessage(content=SYSTEM_PROMPT))
        inputs.append(HumanMessage(content=user_message))

        result = compiled.invoke(
            {"messages": inputs, "session_id": session_id},
            config=config,
        )

        # 核心：while 循环处理"可能多次中断"
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value  # 待审批内容（{tool, args}）
            decision = ask_human(payload)               # 收集人类决策
            # 无论 approve 还是 reject 都恢复图，让图内部节点处理决策
            result = compiled.invoke(Command(resume=decision), config=config)

        # 循环结束 = 图正常跑完，取最终答案
        for m in reversed(result["messages"]):
            if isinstance(m, AIMessage):
                return m.content or ""
        return "未获得回答"
