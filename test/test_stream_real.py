"""真·逐 token 流式端到端测试。

用 MockProvider（按字符 yield）验证：
1. stream_graph_events 产出多个 token 事件（不是一次性返回全文）
2. 每个 token 事件的 content 是短片段
3. 所有 token 拼接起来等于完整回复
4. 最后有 done 事件
"""

import pytest

from app import stream_graph as sg
from app.providers.mock import MockProvider


REPLY = "你好，这是流式测试回复。"


@pytest.fixture
def mock_provider():
    """把模块级 provider 替换成 MockProvider，测试后恢复。"""
    original = sg.provider
    sg.provider = MockProvider(fixed_reply=REPLY)
    yield sg.provider
    sg.provider = original


def test_tokens_are_incremental(mock_provider):
    """token 事件是逐字增量，不是整段一次性返回。"""
    events = list(sg.stream_graph_events("你好", session_id="t-real-stream"))

    tokens = [data for etype, data in events if etype == "token"]
    # MockProvider 按字符 yield，所以 token 事件数应等于回复字符数
    assert len(tokens) == len(REPLY), f"期望 {len(REPLY)} 个 token 片段，实际 {len(tokens)}"
    # 每个片段都是单个字符
    assert all(len(t) <= 2 for t in tokens)  # 中文占 1 个字符


def test_tokens_concatenate_to_full_reply(mock_provider):
    """所有 token 拼接起来等于完整回复。"""
    events = list(sg.stream_graph_events("你好", session_id="t-real-stream2"))
    tokens = [data for etype, data in events if etype == "token"]
    assert "".join(tokens) == REPLY


def test_done_event_at_end(mock_provider):
    """最后一个事件是 done。"""
    events = list(sg.stream_graph_events("你好", session_id="t-real-stream3"))
    assert events[-1] == ("done", None)


def test_no_tool_call_for_plain_answer(mock_provider):
    """普通回答（无 tool_calls）不产生 tool_call / tool_result 事件。"""
    events = list(sg.stream_graph_events("你好", session_id="t-real-stream4"))
    assert not any(etype == "tool_call" for etype, _ in events)
    assert not any(etype == "tool_result" for etype, _ in events)
