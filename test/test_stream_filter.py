"""流式 token 过滤回归测试（修复"工具原始结果先打字一遍再接 AI 总结"）。

根因：stream_mode="messages" 产出图执行期间所有新增消息，包括 tools 节点写入
state 的完整 ToolMessage；stream_graph_events 此前不做过滤，把工具原始输出当
token 推给前端，导致气泡开头先出现工具结果再接 AI 总结。

修复：双重过滤 —— metadata.langgraph_node == "agent" + AI 消息类型
（AIMessage/AIMessageChunk 都放行，因为非流式 provider 产出的是完整 AIMessage）。

补充覆盖：非流式 provider 下 agent 返回完整 AIMessage（有 tool_calls、无
tool_call_chunks），此前 tool_call 事件从不触发，现在用 tool_calls 补发。
"""

import pytest

from app import stream_graph as sg
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage


class FakeCompiled:
    """模拟 compiled.stream(stream_mode="messages") 的产出序列（(chunk, metadata) 元组）。"""

    def stream(self, _inputs, config=None, stream_mode=None):
        yield (  # tools 节点产物：修复前会被误当 token 推送
            ToolMessage(
                content="2026-09-04 17:52:07",
                tool_call_id="call_repro",
                name="get_current_time",
            ),
            {"langgraph_node": "tools"},
        )
        yield (  # agent 发起工具调用（非流式 provider：完整 AIMessage）
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_current_time", "args": {}, "id": "call_repro",
                     "type": "tool_call"}
                ],
            ),
            {"langgraph_node": "agent"},
        )
        yield (  # agent 总结（非流式：整条 AIMessage）
            AIMessage(content="现在是 17:52:07。"),
            {"langgraph_node": "agent"},
        )
        yield (  # 双保险用例：即便 tools 节点产出了 AI 消息也必须过滤
            AIMessageChunk(content="leak"),
            {"langgraph_node": "tools"},
        )


@pytest.fixture
def events():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sg, "build_stream_graph", lambda checkpointer=None: FakeCompiled())
        return list(sg.stream_graph_events("现在几点了", session_id="t-stream-filter"))


def test_tool_result_not_streamed_as_token(events):
    """ToolMessage 的原始输出不得出现在 token 事件里。"""
    tokens = [data for etype, data in events if etype == "token"]
    assert "2026-09-04 17:52:07" not in tokens
    assert all("leak" != t for t in tokens)  # tools 节点的 AI 消息同样被过滤


def test_llm_tokens_and_tool_call_still_streamed(events):
    """agent 回答照常推送；tool_call 带参数；tool_result 以独立事件输出。"""
    tokens = [data for etype, data in events if etype == "token"]
    assert tokens == ["现在是 17:52:07。"]
    # tool_call 事件带参数（前端时间线展示"调了什么、传了什么"）
    assert ("tool_call", {"name": "get_current_time", "args": {}}) in events
    # ToolMessage 不再被静默丢弃：转成 tool_result 事件供前端完成对应步骤
    assert ("tool_result", {"name": "get_current_time",
                            "content": "2026-09-04 17:52:07"}) in events
    assert events[-1] == ("done", None)
