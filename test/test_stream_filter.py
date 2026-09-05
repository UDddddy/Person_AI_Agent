"""流式事件过滤回归测试（stream_mode="custom" 架构）。

新架构下 agent_node / tool_node 各自通过 StreamWriter 推送明确类型的事件，
不会再出现"工具结果被当成 token"的混淆。本测试验证事件解包和类型隔离。

FakeCompiled 模拟 compiled.stream(stream_mode="custom") 的产出：
每个 yield 是一个 {"type": ..., "content": ...} dict（即 StreamWriter 写入的数据）。
"""

import pytest

from app import stream_graph as sg


class FakeCompiled:
    """模拟 compiled.stream(stream_mode="custom") 的产出序列。"""

    def get_state(self, config):
        """模拟空会话（无历史），让 _prepare_inputs 注入 system prompt。"""
        class _State:
            values = {}
        return _State()

    def stream(self, _inputs, config=None, stream_mode=None):
        # agent 发起工具调用（StreamWriter 推送 tool_call，content 是 {name, args}）
        yield {"type": "tool_call", "content": {"name": "get_current_time", "args": {}}}
        # agent 逐 token 输出总结
        yield {"type": "token", "content": "现在是 "}
        yield {"type": "token", "content": "17:52:07。"}
        # tool_node 推送工具结果（独立事件，不会混入 token）
        yield {"type": "tool_result", "content": {
            "name": "get_current_time", "content": "2026-09-04 17:52:07",
        }}


@pytest.fixture
def events():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sg, "build_stream_graph", lambda checkpointer=None: FakeCompiled())
        return list(sg.stream_graph_events("现在几点了", session_id="t-stream-filter"))


def test_tool_result_not_streamed_as_token(events):
    """工具结果不得出现在 token 事件里（类型隔离）。"""
    tokens = [data for etype, data in events if etype == "token"]
    assert "2026-09-04 17:52:07" not in tokens
    assert all("get_current_time" not in str(t) for t in tokens)


def test_tokens_streamed_incrementally(events):
    """token 事件是增量片段（不是整条一次性返回）。"""
    tokens = [data for etype, data in events if etype == "token"]
    assert tokens == ["现在是 ", "17:52:07。"]
    assert len(tokens) > 1  # 至少两个片段，证明是逐 token 而非整段


def test_tool_call_and_tool_result_events(events):
    """tool_call 和 tool_result 以独立事件输出。"""
    assert ("tool_call", {"name": "get_current_time", "args": {}}) in events
    assert ("tool_result", {"name": "get_current_time",
                            "content": "2026-09-04 17:52:07"}) in events


def test_done_event_at_end(events):
    """最后一个事件是 done。"""
    assert events[-1] == ("done", None)
