"""阶段 3 · V3 LangGraph Agent 自动化测试。

通过 patch `app.graph_agent.call_llm` mock LLM，让测试完全离线可跑，
验证图的路由逻辑、工具执行与 Checkpoint 持久化。
"""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from app.graph_agent import (
    END,
    build_graph,
    graph,
    should_continue,
    tool_node,
)

SYSTEM = SystemMessage(content="You are a helpful assistant.")


def test_direct_answer_no_tool_call():
    """LLM 直接回答（无 tool_calls）→ 图返回最终答案，历史累积。"""
    fake = AIMessage(content="1+1 等于 2")
    with patch("app.graph_agent.call_llm", return_value=fake):
        result = graph.invoke(
            {"messages": [SYSTEM, HumanMessage(content="1+1 等于几")], "session_id": "t1"}
        )

    last = result["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.content == "1+1 等于 2"
    assert last.tool_calls is None or last.tool_calls == []
    # 完整消息流：system + user + assistant
    assert len(result["messages"]) == 3


def test_tool_call_chain():
    """LLM 先请求计算器工具 → 工具执行 → LLM 基于结果给出最终答案。"""
    calls = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "calculator",
                    "args": {"expression": "1+1"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="1+1 等于 2"),
    ]
    with patch("app.graph_agent.call_llm", side_effect=calls) as mocked:
        result = graph.invoke(
            {"messages": [SYSTEM, HumanMessage(content="计算 1+1")], "session_id": "t2"}
        )

    # LLM 被调用两次：先决策调工具，再基于工具结果回答
    assert mocked.call_count == 2
    # 消息流中应包含 tool 角色的消息
    roles = [type(m).__name__ for m in result["messages"]]
    assert "ToolMessage" in roles
    # 工具结果正确（BaseTool.execute 返回字符串 "2"）
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs[0].content == "2"
    # 最终回答
    assert result["messages"][-1].content == "1+1 等于 2"


def test_should_continue_routing():
    """条件边：有 tool_calls → 'tools'；否则 → END。"""
    no_tool_state = {"messages": [AIMessage(content="hi")], "session_id": "x"}
    assert should_continue(no_tool_state) == END

    tool_state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculator",
                        "args": {"expression": "1"},
                        "id": "c",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "session_id": "x",
    }
    assert should_continue(tool_state) == "tools"


def test_tool_node_executes_real_tool():
    """工具节点真实执行 calculator，返回 ToolMessage 并携带 tool_call_id。"""
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculator",
                        "args": {"expression": "2+3"},
                        "id": "call_9",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "session_id": "x",
    }
    result = tool_node(state)
    msg = result["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.content == "5"
    assert msg.tool_call_id == "call_9"


def test_checkpoint_persistence(tmp_path):
    """Checkpoint：同一 thread_id 重启后历史仍可恢复。"""
    db = tmp_path / "ck.sqlite"

    # 第一次会话：写入历史
    with SqliteSaver.from_conn_string(str(db)) as cp:
        g = build_graph(checkpointer=cp)
        config = {"configurable": {"thread_id": "sess-1"}}
        with patch(
            "app.graph_agent.call_llm", return_value=AIMessage(content="记住了")
        ):
            g.invoke(
                {"messages": [SYSTEM, HumanMessage(content="记住事实X")]},
                config=config,
            )

    # 模拟服务重启：重新打开 checkpointer，同一 thread_id 历史应还在
    with SqliteSaver.from_conn_string(str(db)) as cp:
        g = build_graph(checkpointer=cp)
        config = {"configurable": {"thread_id": "sess-1"}}
        state = g.get_state(config)
        msgs = state.values["messages"]
        assert len(msgs) >= 2  # system + user
        assert any("记住事实X" in getattr(m, "content", "") for m in msgs)


def test_checkpoint_thread_isolation(tmp_path):
    """Checkpoint：不同 thread_id 的会话相互隔离。"""
    db = tmp_path / "ck2.sqlite"

    with SqliteSaver.from_conn_string(str(db)) as cp:
        g = build_graph(checkpointer=cp)
        with patch(
            "app.graph_agent.call_llm", return_value=AIMessage(content="收到")
        ):
            g.invoke(
                {"messages": [SYSTEM, HumanMessage(content="只属于会话A")]},
                config={"configurable": {"thread_id": "A"}},
            )

        state_b = g.get_state({"configurable": {"thread_id": "B"}})
        assert not state_b.values.get("messages")  # B 会话无历史
