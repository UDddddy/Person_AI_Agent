# from app.agent import run_agent

# def test_calculator_agent():
#     result = run_agent("请计算1+1")

#     print("\n最终结果是：")
#     print(result)
#     assert result is not None


from unittest.mock import MagicMock,patch
from app.agent import run_agent

def make_tool_call(name,arguments):

    """构造一个假的 tool_call 对象"""
    tc = MagicMock()
    tc.id = "call_test"
    tc.function.name = name
    tc.function.arguments = arguments
    return tc

def make_message(content=None, tool_calls=None):
    """构造一个假的 OpenAI message 对象"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    return msg
def test_agent_tool_then_answer():
    """场景1：第一轮要调工具，第二轮给最终答案"""
    fake_responses = [
        make_message(tool_calls=[make_tool_call("calculator", '{"expression": "1+1"}')]),
        make_message(content="1+1 等于 2"),
    ]
    with patch("app.agent.chat_with_tools", side_effect=fake_responses) as mock_chat:
        result = run_agent("请计算 1+1")

    assert result == "1+1 等于 2"
    assert mock_chat.call_count == 2   # 正好调了两次


def test_agent_reaches_max_iterations():
    """场景2：LLM 一直要调工具，达到上限返回兜底消息"""
    fake = make_message(tool_calls=[make_tool_call("calculator", '{"expression": "1+1"}')])
    with patch("app.agent.chat_with_tools", return_value=fake) as mock_chat:
        result = run_agent("请计算 1+1", max_iterations=3)
    assert result == "达到最大迭代次数，未能得到最终答案。"
    assert mock_chat.call_count == 3