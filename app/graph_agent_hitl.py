"""基于 LangGraph 的 Agent 编排（阶段 3 · V3 · HITL 人工审批版）。

与 app/graph_agent.py 的关系：这是它的"带人工审批"版本。
区别只有一处核心——tools 节点会对**危险工具**（run_command）执行人工审批：

    START → agent ──(有 tool_calls)──> tools ──(危险工具 interrupt)──> agent ── ...
                └──(无 tool_calls)──> END

HITL 机制（LangGraph interrupt）：
- 节点内调用 `interrupt(value)` → 图暂停，把 value 抛给外部（进入 __interrupt__）。
- 外部用 `Command(resume=...)` 恢复 → 节点从头重跑，interrupt 返回 resume 值。
- 因此 interrupt **必须**配 checkpointer（中断依赖持久化图状态）。

阶段 5-7 集成改造（2026-09-04）：
- LLM 调用复用主链路 app.graph_agent.call_llm（provider 抽象 + 指标上报），
  消除原先两份重复的 to_openai_messages / call_llm；
- Checkpoint 复用 graph_agent.get_checkpointer（同一 checkpoints.sqlite）；
- 工具执行改走 ToolPipeline（create_pipeline(include_approval=False)）——
  注意审批路径互斥：人工审批由 interrupt 完成，管道里不能再挂 approval
  扩展，否则 BEFORE_TOOL 钩子会先拦下危险工具，interrupt 永远收不到请求。

四个对外入口：
- build_graph(checkpointer)：构造 HITL 图（checkpointer 必填）。
- run_hitl(user_message, session_id, ask_human)：两段式驱动，命令行/回调收集决策（demo 用）。
- start_hitl_turn(...)：API 两段式第 1 段——发起对话，遇危险工具返回待审批 payload。
- resume_hitl_turn(...)：API 两段式第 2 段——提交 approve/reject，恢复执行。
"""

import logging

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.composition_root import create_pipeline
from app.graph_agent import call_llm, get_checkpointer
from app.graph_state import AgentState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are a helpful assistant."

# 危险工具集合：这些工具执行前必须人工审批
DANGEROUS_TOOLS = {"run_command"}

# HITL 专用管道：不挂 approval 扩展（审批由 interrupt 完成，两路互斥，见模块 docstring）
pipeline = create_pipeline(include_approval=False)


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
    - 执行统一走 ToolPipeline：参数校验、审计日志、耗时统计与主链路一致；
      拒绝分支不进管道，直接回"被拒"ToolMessage。
    """
    last = state["messages"][-1]
    tool_messages = []
    for tc in last.tool_calls:
        if tc["name"] in DANGEROUS_TOOLS:
            logger.warning("危险工具 %s 待审批，参数 %s", tc["name"], tc["args"])
            # 暂停图，把"待审批内容"抛给外部；恢复后这里返回外部决策
            decision = interrupt({"tool": tc["name"], "args": tc["args"]})
            if decision == "approve":
                result = pipeline.execute(tc["name"], tc["args"])
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
            result = pipeline.execute(tc["name"], tc["args"])
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
# 结果归一化：invoke 的返回 → API 友好的 dict
# ---------------------------------------------------------------------------

def _resolve_result(result) -> dict:
    """把一次 invoke 的结果归一化：
    - 带 __interrupt__：返回 {"status": "interrupted", "pending_approval": {tool, args}}
      （一次 invoke 最多暂停在第一个 interrupt，多个危险工具由前端多次 resume 解决）
    - 正常跑完：返回 {"status": "done", "reply": 最终 AIMessage 文本}
    """
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        return {"status": "interrupted", "pending_approval": payload}

    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage):
            return {"status": "done", "reply": m.content or ""}
    return {"status": "done", "reply": "未获得回答"}


# ---------------------------------------------------------------------------
# HITL 两段式驱动 A：回调式（demo / 测试用）
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


# ---------------------------------------------------------------------------
# HITL 两段式驱动 B：HTTP API 式（每次请求只走一段，状态由 checkpointer 承载）
# ---------------------------------------------------------------------------

def _has_pending_interrupt(compiled, config) -> bool:
    """检查该 thread 是否停在 interrupt 上（有待审批项）。"""
    state = compiled.get_state(config)
    return any(getattr(t, "interrupts", None) for t in state.tasks)


def start_hitl_turn(user_message: str, session_id: str, checkpointer) -> dict:
    """API 两段式第 1 段：发起一轮对话。

    返回：
    - {"status": "interrupted", "pending_approval": {"tool", "args"}}
      危险工具触发审批，前端展示后调 /resume 提交决策；
    - {"status": "done", "reply": "..."}
      本轮没有触发审批，直接返回最终回答。
    """
    compiled = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    inputs = []
    current = compiled.get_state(config)
    if not current.values.get("messages"):
        inputs.append(SystemMessage(content=SYSTEM_PROMPT))
    inputs.append(HumanMessage(content=user_message))

    result = compiled.invoke(
        {"messages": inputs, "session_id": session_id},
        config=config,
    )
    return _resolve_result(result)


def resume_hitl_turn(decision: str, session_id: str, checkpointer) -> dict:
    """API 两段式第 2 段：提交人工决策（"approve" / "reject"），恢复执行。

    返回结构与 start_hitl_turn 一致——若本轮还有第二个危险工具，
    会再次返回 interrupted，前端继续审批即可。
    会话不在中断状态时返回 {"status": "no_pending_approval"}。
    """
    compiled = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    if not _has_pending_interrupt(compiled, config):
        return {"status": "no_pending_approval"}

    result = compiled.invoke(Command(resume=decision), config=config)
    return _resolve_result(result)
