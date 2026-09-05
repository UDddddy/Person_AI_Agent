"""阶段6 · 树形会话存储 + Compaction 压缩 单元测试。

- TestCompaction：纯算法（token估算/安全切割点/压缩），不碰数据库
- TestSessionStore：树形存储（append/build_chain/fork/travel/compact），
  每个测试用 tmp_path 隔离数据库
- TestSessionAdapter：entry ↔ LangChain Message 互转
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app import session_store as ss
from app.session_store import (
    init_store,
    append_entry,
    get_entry,
    get_leaf,
    build_chain,
    get_children,
    fork_session,
    travel_to,
    compact_session,
)
from app.compaction import (
    estimate_tokens,
    entry_tokens,
    chain_tokens,
    find_safe_cut_point,
    compact_chain,
    should_compact,
)
from app.session_adapter import (
    entry_to_message,
    chain_to_messages,
    append_message_entry,
    persist_messages,
)


# ===========================================================================
# Compaction 纯算法
# ===========================================================================

class TestCompaction:
    def test_estimate_tokens_chinese_heavier_than_english(self):
        """中文每字 token 比英文重。"""
        assert estimate_tokens("你好世界") > estimate_tokens("abcd")

    def test_estimate_tokens_empty(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0

    def test_no_compression_when_under_budget(self):
        chain = [
            {"type": "user", "content": "hi", "tool_calls": None},
            {"type": "assistant", "content": "hello", "tool_calls": None},
        ]
        assert find_safe_cut_point(chain, 10000) == 0
        assert not should_compact(chain, 10000)

    def test_safe_cut_preserves_tool_pair(self):
        """不能在 assistant(tool_calls) 和 tool 结果之间切。"""
        chain = [
            {"type": "user", "content": "x" * 50, "tool_calls": None},
            {"type": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
            {"type": "tool", "content": "r", "tool_call_id": "t1"},
            {"type": "assistant", "content": "done", "tool_calls": None},
        ]
        cut = find_safe_cut_point(chain, 15)
        # 切割点之前的消息必须工具配对完整：不能切在 index 1（只有调用没结果）
        before = chain[:cut]
        pending = set()
        for e in before:
            if e["type"] == "assistant" and e.get("tool_calls"):
                for tc in e["tool_calls"]:
                    pending.add(tc["id"])
            if e["type"] == "tool":
                pending.discard(e.get("tool_call_id"))
        assert pending == set(), f"切割点 {cut} 留下了未配对的工具调用 {pending}"

    def test_compact_chain_structure(self):
        chain = [
            {"type": "user", "content": "a" * 100, "tool_calls": None},
            {"type": "assistant", "content": "b", "tool_calls": None},
        ]
        new_chain = compact_chain(chain, 5, "摘要")
        assert new_chain[0]["type"] == "compaction"
        assert new_chain[0]["content"] == "摘要"
        assert new_chain[0]["metadata"]["cut_index"] >= 1

    def test_compact_chain_no_op_under_budget(self):
        chain = [{"type": "user", "content": "hi", "tool_calls": None}]
        result = compact_chain(chain, 10000, "摘要")
        assert len(result) == 1
        assert result[0]["type"] == "user"  # 原样返回，不插摘要

    def test_chain_tokens_sums_entries(self):
        chain = [
            {"type": "user", "content": "hello", "tool_calls": None},
            {"type": "assistant", "content": "world", "tool_calls": None},
        ]
        assert chain_tokens(chain) == entry_tokens(chain[0]) + entry_tokens(chain[1])


# ===========================================================================
# SessionStore 树形存储（每个测试隔离数据库）
# ===========================================================================

@pytest.fixture
def store(tmp_path, monkeypatch):
    """每个测试用独立临时数据库。"""
    db_path = tmp_path / "test_sessions.db"
    monkeypatch.setattr(ss, "DB_PATH", db_path)
    init_store()
    return db_path


class TestSessionStore:
    def test_append_and_get_entry(self, store):
        e = append_entry("s1", None, "user", "你好")
        fetched = get_entry(e["id"])
        assert fetched["content"] == "你好"
        assert fetched["session_id"] == "s1"
        assert fetched["parent_id"] is None

    def test_get_entry_nonexistent_returns_none(self, store):
        assert get_entry("nonexistent_id") is None

    def test_get_leaf_returns_latest(self, store):
        a = append_entry("s1", None, "user", "a")
        b = append_entry("s1", a["id"], "assistant", "b")
        leaf = get_leaf("s1")
        assert leaf["id"] == b["id"]

    def test_build_chain_root_to_leaf_order(self, store):
        a = append_entry("s1", None, "user", "a")
        b = append_entry("s1", a["id"], "assistant", "b")
        c = append_entry("s1", b["id"], "user", "c")
        chain = build_chain("s1")
        assert [e["id"] for e in chain] == [a["id"], b["id"], c["id"]]

    def test_build_chain_from_specific_leaf(self, store):
        a = append_entry("s1", None, "user", "a")
        b = append_entry("s1", a["id"], "assistant", "b")
        append_entry("s1", b["id"], "user", "c")
        chain = build_chain("s1", leaf_id=b["id"])
        assert len(chain) == 2  # 只到 b

    def test_build_chain_empty_session(self, store):
        assert build_chain("empty") == []

    def test_tool_calls_json_roundtrip(self, store):
        tc = [{"id": "t1", "name": "calc", "args": {"x": 1}}]
        e = append_entry("s1", None, "assistant", "", tool_calls=tc)
        fetched = get_entry(e["id"])
        assert fetched["tool_calls"] == tc  # 存 JSON 读回 dict

    def test_fork_session_crosses_history(self, store):
        a = append_entry("s1", None, "user", "原始")
        b = append_entry("s1", a["id"], "assistant", "回答")
        fork = fork_session(b["id"], "s2")
        append_entry("s2", fork["id"], "user", "新分支")
        # 新会话 build_chain 能回溯到原会话历史
        chain2 = build_chain("s2")
        contents = [e["content"] for e in chain2]
        assert "原始" in contents
        assert "新分支" in contents
        assert any(e["type"] == "fork" for e in chain2)

    def test_fork_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            fork_session("nonexistent", "s2")

    def test_travel_to_validates_session(self, store):
        a = append_entry("s1", None, "user", "a")
        assert travel_to("s1", a["id"])["id"] == a["id"]
        with pytest.raises(ValueError):
            travel_to("s1", "nonexistent")
        with pytest.raises(ValueError):
            travel_to("other_session", a["id"])

    def test_get_children_multiple_branches(self, store):
        a = append_entry("s1", None, "user", "a")
        append_entry("s1", a["id"], "assistant", "分支1")
        append_entry("s1", a["id"], "assistant", "分支2")
        children = get_children(a["id"])
        assert len(children) == 2

    def test_time_travel_grows_new_branch(self, store):
        """从历史节点 append 新消息，原分支保留，当前链走新分支。"""
        a = append_entry("s1", None, "user", "a")
        b = append_entry("s1", a["id"], "assistant", "b")
        c = append_entry("s1", b["id"], "user", "c-原分支")
        # 从 b 时间旅行，长出 e
        e = append_entry("s1", b["id"], "user", "e-新分支")
        chain = build_chain("s1")  # 叶子是 e（timestamp 最新）
        ids = [x["id"] for x in chain]
        assert e["id"] in ids
        assert c["id"] not in ids  # 原分支 c 不在当前链
        assert get_entry(c["id"]) is not None  # 但仍在库中

    def test_compact_session_force(self, store):
        for i, txt in enumerate(["消息1", "消息2", "消息3"]):
            parent = get_leaf("s1")["id"] if get_leaf("s1") else None
            append_entry("s1", parent, "user", txt)
        new_chain = compact_session("s1", 100000, "手动摘要", force=True)
        assert new_chain[0]["type"] == "compaction"
        rebuilt = build_chain("s1")
        assert rebuilt[0]["type"] == "compaction"

    def test_compact_session_under_threshold_no_op(self, store):
        append_entry("s1", None, "user", "hi")
        result = compact_session("s1", 100000, "摘要")
        assert all(e["type"] != "compaction" for e in result)


# ===========================================================================
# SessionAdapter 互转
# ===========================================================================

class TestSessionAdapter:
    def test_entry_to_user_message(self):
        msg = entry_to_message({"type": "user", "content": "你好"})
        assert isinstance(msg, HumanMessage)
        assert msg.content == "你好"

    def test_entry_to_assistant_with_tool_calls(self):
        msg = entry_to_message({
            "type": "assistant", "content": "",
            "tool_calls": [{"id": "t1", "name": "calc", "args": {"x": 1}}],
        })
        assert isinstance(msg, AIMessage)
        assert msg.tool_calls[0]["name"] == "calc"
        assert msg.tool_calls[0]["args"] == {"x": 1}
        assert msg.tool_calls[0]["id"] == "t1"

    def test_entry_to_tool_message(self):
        msg = entry_to_message({
            "type": "tool", "content": "结果", "tool_call_id": "t1",
        })
        assert isinstance(msg, ToolMessage)
        assert msg.tool_call_id == "t1"

    def test_compaction_entry_becomes_system_message(self):
        msg = entry_to_message({"type": "compaction", "content": "历史摘要"})
        assert isinstance(msg, SystemMessage)
        assert "历史摘要" in msg.content

    def test_fork_entry_returns_none(self):
        assert entry_to_message({"type": "fork", "content": "标记"}) is None

    def test_chain_to_messages_filters_fork(self, store):
        a = append_entry("s1", None, "user", "a")
        fork = fork_session(a["id"], "s2")
        append_entry("s2", fork["id"], "user", "b")
        chain = build_chain("s2")
        msgs = chain_to_messages(chain)
        # fork 节点被过滤，其余转成消息
        assert all(not isinstance(m, type(None)) for m in msgs)
        assert len(msgs) == 2  # a + b

    def test_persist_messages_chains_parent(self, store):
        msgs = [HumanMessage(content="第一"), AIMessage(content="第二")]
        entries = persist_messages("s1", msgs)
        assert len(entries) == 2
        assert entries[0]["type"] == "user"
        assert entries[1]["type"] == "assistant"
        assert entries[1]["parent_id"] == entries[0]["id"]  # parent 链衔接
        leaf = get_leaf("s1")
        assert leaf["id"] == entries[1]["id"]
