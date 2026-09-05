"""会话历史端点（GET /api/sessions/{id}/history）自动化测试。

- monkeypatch app.session_store.DB_PATH → 临时库，不污染真实 sessions.db；
- 用 append_entry 直接种一条完整链（user → assistant(带 tool_calls) → tool → assistant），
  外加 fork 标记节点，验证内部节点被过滤。

覆盖：
1. 完整链回放：顺序正确、tool 结果带 tool_call_id 供前端配对
2. 内部节点过滤：fork / compaction / system 不出现在响应里
3. 空会话/不存在会话：返回 count=0 + 空数组，不 500
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.session_store import append_entry, init_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient + 临时树形存储库（patch DB_PATH 路径隔离）。"""
    monkeypatch.setattr("app.session_store.DB_PATH", str(tmp_path / "history_api.sqlite"))
    init_store()  # 临时库先建表，种子数据才能写入
    with TestClient(app) as c:
        yield c


def _seed_session(session_id):
    """种一条完整链并返回各节点 id：user → assistant(tool_call) → tool → assistant。"""
    e1 = append_entry(session_id, None, "user", "帮我算一下 7*6")
    e2 = append_entry(
        session_id, e1["id"], "assistant", "",
        tool_calls=[{"name": "calculator", "args": {"expression": "7*6"},
                     "id": "call_hist_1", "type": "tool_call"}],
    )
    e3 = append_entry(session_id, e2["id"], "tool", "42", tool_call_id="call_hist_1")
    e4 = append_entry(session_id, e3["id"], "assistant", "7*6 等于 42")
    return e1, e2, e3, e4


def test_history_returns_full_chain(client):
    """完整链回放：消息顺序正确，工具结果带 tool_call_id，assistant 带 tool_calls。"""
    _seed_session("hist-full")
    r = client.get("/api/sessions/hist-full/history")
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "hist-full"
    assert data["count"] == 4
    msgs = data["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[0]["content"] == "帮我算一下 7*6"
    assert msgs[1]["tool_calls"][0]["name"] == "calculator"
    assert msgs[2]["tool_call_id"] == "call_hist_1"
    assert msgs[2]["content"] == "42"
    assert msgs[3]["content"] == "7*6 等于 42"


def test_history_filters_internal_nodes(client):
    """fork / compaction / system 是内部节点，不出现在响应中。"""
    e1, _, _, e4 = _seed_session("hist-filter")
    append_entry("hist-other", e4["id"], "fork", "Forked from hist-filter at xxx")  # 跨会话 fork 标记
    append_entry("hist-filter", e4["id"], "compaction", "历史摘要：之前聊了计算器")
    append_entry("hist-filter", e4["id"], "system", "你是助手")

    r = client.get("/api/sessions/hist-filter/history")
    assert r.status_code == 200
    data = r.json()
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]  # 内部节点全部被过滤
    assert data["count"] == 4


def test_history_empty_session(client):
    """空会话 / 不存在的会话：count=0 + 空数组，不 500。"""
    r = client.get("/api/sessions/no-such-session/history")
    assert r.status_code == 200
    assert r.json() == {"session_id": "no-such-session", "count": 0, "messages": []}
