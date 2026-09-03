"""阶段 3 · HITL 人工审批 自动化测试。

通过 patch `app.graph_agent_hitl.call_llm` mock LLM，离线验证：
- 危险工具（run_command）会触发中断，批准后执行、拒绝后返回"被拒"消息；
- 安全工具（calculator）不触发中断，直接执行。
run_hitl 通过 ask_human 回调传入固定决策，避免 input() 阻塞测试。
"""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph_agent_hitl import run_hitl

# 危险工具调用（LLM 第一次决策会请求它）
RUN_CMD_CALL = {
    "name": "run_command",
    "args": {"command": "ls"},
    "id": "call_run",
    "type": "tool_call",
}
# 安全工具调用
CALC_CALL = {
    "name": "calculator",
    "args": {"expression": "1+1"},
    "id": "call_calc",
    "type": "tool_call",
}


def test_hitl_dangerous_tool_approve():
    """危险工具 + 批准：执行工具 → LLM 基于结果作答。"""
    def fake_llm(messages):
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(content="", tool_calls=[RUN_CMD_CALL])
        return AIMessage(content="命令已执行，输出: file1 file2")

    with patch("app.graph_agent_hitl.call_llm", side_effect=fake_llm) as mocked:
        ans = run_hitl("帮我跑一下 ls", "sess-a", ask_human=lambda p: "approve")

    assert mocked.call_count == 2
    assert ans == "命令已执行，输出: file1 file2"


def test_hitl_dangerous_tool_reject():
    """危险工具 + 拒绝：不执行，LLM 收到"被拒"ToolMessage 后改口。"""
    def fake_llm(messages):
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(content="", tool_calls=[RUN_CMD_CALL])
        # LLM 第二次收到的是"被拒"ToolMessage
        last = messages[-1]
        assert isinstance(last, ToolMessage)
        assert "拒绝" in last.content
        assert last.tool_call_id == "call_run"
        return AIMessage(content="好的，那我不执行了")

    with patch("app.graph_agent_hitl.call_llm", side_effect=fake_llm) as mocked:
        ans = run_hitl("帮我跑一下 ls", "sess-r", ask_human=lambda p: "reject")

    assert mocked.call_count == 2
    assert "不执行" in ans


def test_hitl_safe_tool_no_interrupt():
    """安全工具（calculator）：不触发中断，直接执行。"""
    def fake_llm(messages):
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(content="", tool_calls=[CALC_CALL])
        return AIMessage(content="1+1 等于 2")

    with patch("app.graph_agent_hitl.call_llm", side_effect=fake_llm) as mocked:
        # ask_human 传一个"永远不会被调用"的回调：安全工具不该触发审批
        def never_called(_):
            raise AssertionError("安全工具不应触发人工审批")

        ans = run_hitl("计算 1+1", "sess-s", ask_human=never_called)

    assert mocked.call_count == 2
    assert ans == "1+1 等于 2"


def test_hitl_multiple_dangerous_tools():
    """同一条消息触发多个危险工具：while 循环应处理多次中断。"""
    second_call = {
        "name": "run_command",
        "args": {"command": "pwd"},
        "id": "call_run2",
        "type": "tool_call",
    }
    decisions = []

    def fake_llm(messages):
        if isinstance(messages[-1], HumanMessage):
            # 第一次请求两个危险工具
            return AIMessage(content="", tool_calls=[RUN_CMD_CALL, second_call])
        return AIMessage(content="两个命令都执行完了")

    def ask_human(payload):
        decisions.append(payload["args"]["command"])
        return "approve"

    with patch("app.graph_agent_hitl.call_llm", side_effect=fake_llm) as mocked:
        ans = run_hitl("跑两个命令", "sess-m", ask_human=ask_human)

    assert mocked.call_count == 2
    assert decisions == ["ls", "pwd"]  # 两次中断都经过人工决策
    assert ans == "两个命令都执行完了"
