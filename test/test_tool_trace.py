"""工具调用轨迹（tool_trace / 工具时间线数据源）测试。

- pipeline 层：ToolPipeline.execute 按调用顺序记录结构化轨迹
  {name, args, result, latency, blocked}，拦截/参数缺失也如实记录；
- API 层：/api/chat 响应体带 tool_trace（走 run_graph_agent_detailed），
  LLM 发起工具调用时轨迹完整可回放，纯聊天时为空列表。

隔离：patch app.session_store.DB_PATH → 临时树形存储；
     patch app.graph_agent.call_llm → 离线假 LLM。
"""

import pytest
from fastapi.testclient import TestClient

from langchain_core.messages import AIMessage

from app.main import app
from app.session_store import init_store

CALC_CALL = {
    "name": "calculator",
    "args": {"expression": "7*6"},
    "id": "call_trace_1",
    "type": "tool_call",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient + 临时树形存储 + 离线 LLM（一轮工具调用后作答）。"""
    monkeypatch.setattr("app.session_store.DB_PATH", str(tmp_path / "trace_api.sqlite"))
    init_store()

    def fake_llm(_messages):
        if _messages and getattr(_messages[-1], "tool_call_id", None) == "call_trace_1":
            return AIMessage(content="7*6 等于 42")
        return AIMessage(content="", tool_calls=[CALC_CALL])

    monkeypatch.setattr("app.graph_agent.call_llm", fake_llm)
    with TestClient(app) as c:
        yield c


def test_pipeline_trace_records_order():
    """pipeline 层：轨迹按执行顺序追加，成功/失败/拦截分类记录。"""
    from tools.pipeline import ToolPipeline

    p = ToolPipeline()
    p.execute("calculator", {"expression": "1+1"})
    p.execute("no_such_tool", {})                    # 工具不存在 → blocked
    p.execute("hash_text", {"algorithm": "md5"})     # 缺必填参数 → blocked

    assert [t["name"] for t in p.trace] == ["calculator", "no_such_tool", "hash_text"]
    ok, missing, incomplete = p.trace
    assert ok["blocked"] is False and "2" in ok["result"] and ok["latency"] >= 0
    assert missing["blocked"] is True and missing["result"] == "工具不存在"
    assert incomplete["blocked"] is True and incomplete["result"] == "参数不完整"


def test_chat_api_returns_tool_trace(client):
    """API 层：/api/chat 响应带按顺序排列的 tool_trace。"""
    r = client.post("/api/chat", json={
        "message": "用计算器算 7*6",
        "session_id": "trace-api-test",
    }).json()
    assert r["reply"] == "7*6 等于 42"
    assert len(r["tool_trace"]) == 1
    step = r["tool_trace"][0]
    assert step["name"] == "calculator"
    assert step["args"] == {"expression": "7*6"}
    assert step["blocked"] is False
    assert "42" in step["result"]
    assert step["latency"] >= 0


def test_chat_api_no_tool_empty_trace(client):
    """纯聊天轮次（LLM 直接回答）：tool_trace 为空列表。"""
    def plain_llm(_messages):
        return AIMessage(content="你好呀")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.graph_agent.call_llm", plain_llm)
        r = client.post("/api/chat", json={
            "message": "你好",
            "session_id": "trace-api-plain",
        }).json()
    assert r["reply"] == "你好呀"
    assert r["tool_trace"] == []
