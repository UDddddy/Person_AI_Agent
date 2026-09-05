"""HITL 两段式 API 端点自动化测试。

- monkeypatch app.graph_agent.CHECKPOINT_PATH → 临时库，不污染真实 checkpoints.sqlite；
- patch app.graph_agent_hitl.call_llm → 完全离线；
- TestClient 走完整 HTTP 层，验证 interrupt → resume(approve/reject) 全流程。

覆盖：
1. approve 流程：第 1 段返回 interrupted + 待审批 payload → 第 2 段批准 → 工具执行 → done
2. reject 流程：LLM 收到"被拒"ToolMessage 后改口
3. 安全工具：不触发 interrupt，第 1 段直接 done
4. 无待审批项时调 resume：返回 no_pending_approval（不 500）
5. decision 参数校验：非法值 → 422
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from langchain_core.messages import AIMessage, ToolMessage

from app.main import app

RUN_CMD_CALL = {
    "name": "run_command",
    "args": {"command": "ls"},
    "id": "call_run",
    "type": "tool_call",
}
CALC_CALL = {
    "name": "calculator",
    "args": {"expression": "1+1"},
    "id": "call_calc",
    "type": "tool_call",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient + 临时 checkpoint 库（通过 patch CHECKPOINT_PATH 路径隔离）。"""
    monkeypatch.setattr(
        "app.graph_agent.CHECKPOINT_PATH", str(tmp_path / "hitl_api.sqlite")
    )
    with TestClient(app) as c:
        yield c


def _fake_llm_two_turns(first_answer, second_answer):
    """前 N 次调用依次返回给定答案，之后固定返回最后一个。"""
    calls = [first_answer, second_answer]

    def fake(_messages):
        return calls.pop(0) if calls else second_answer

    return fake


def test_hitl_api_approve_flow(client):
    """危险工具 → interrupted 返回待审批 → resume approve → 工具执行 → done。"""
    with patch(
        "app.graph_agent_hitl.call_llm",
        side_effect=_fake_llm_two_turns(
            AIMessage(content="", tool_calls=[RUN_CMD_CALL]),
            AIMessage(content="命令已执行，输出: file1 file2"),
        ),
    ) as mocked:
        r1 = client.post(
            "/api/chat_hitl",
            json={"message": "帮我跑一下 ls", "session_id": "api-approve"},
        ).json()
        assert r1["status"] == "interrupted"
        assert r1["pending_approval"] == {
            "tool": "run_command",
            "args": {"command": "ls"},
        }
        assert mocked.call_count == 1  # 图暂停在工具节点，LLM 只调了一次

        r2 = client.post(
            "/api/chat_hitl/resume",
            json={"session_id": "api-approve", "decision": "approve"},
        ).json()
        assert r2["status"] == "done"
        assert r2["reply"] == "命令已执行，输出: file1 file2"
        assert mocked.call_count == 2  # 恢复后 LLM 基于工具结果作答


def test_hitl_api_reject_flow(client):
    """拒绝分支：不执行工具，LLM 收到"被拒"ToolMessage 后改口。"""
    def fake(messages):
        if isinstance(messages[-1], ToolMessage):
            assert "拒绝" in messages[-1].content
            assert messages[-1].tool_call_id == "call_run"
            return AIMessage(content="好的，那我不执行了")
        return AIMessage(content="", tool_calls=[RUN_CMD_CALL])

    with patch("app.graph_agent_hitl.call_llm", side_effect=fake):
        r1 = client.post(
            "/api/chat_hitl",
            json={"message": "帮我跑一下 ls", "session_id": "api-reject"},
        ).json()
        assert r1["status"] == "interrupted"

        r2 = client.post(
            "/api/chat_hitl/resume",
            json={"session_id": "api-reject", "decision": "reject"},
        ).json()
        assert r2["status"] == "done"
        assert "不执行" in r2["reply"]


def test_hitl_api_safe_tool_no_interrupt(client):
    """安全工具（calculator）不触发 interrupt：第 1 段直接返回 done。"""
    with patch(
        "app.graph_agent_hitl.call_llm",
        side_effect=_fake_llm_two_turns(
            AIMessage(content="", tool_calls=[CALC_CALL]),
            AIMessage(content="1+1 等于 2"),
        ),
    ):
        r = client.post(
            "/api/chat_hitl",
            json={"message": "计算 1+1", "session_id": "api-safe"},
        ).json()
        assert r["status"] == "done"
        assert r["reply"] == "1+1 等于 2"


def test_hitl_api_resume_without_pending(client):
    """没有待审批项时调 resume：返回 no_pending_approval，不报 500。"""
    r = client.post(
        "/api/chat_hitl/resume",
        json={"session_id": "api-no-pending", "decision": "approve"},
    ).json()
    assert r["status"] == "no_pending_approval"


def test_hitl_api_invalid_decision_rejected(client):
    """decision 校验：非 approve/reject → 422，不进入图执行。"""
    r = client.post(
        "/api/chat_hitl/resume",
        json={"session_id": "api-invalid", "decision": "maybe"},
    )
    assert r.status_code == 422
