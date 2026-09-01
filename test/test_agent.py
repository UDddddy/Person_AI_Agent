# from app.agent import run_agent

# def test_calculator_agent():
#     result = run_agent("请计算1+1")

#     print("\n最终结果是：")
#     print(result)
#     assert result is not None


from unittest.mock import MagicMock,patch
from app.agent import run_agent
from app.db import save_message,load_history
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
def test_agent_tool_then_answer(tmp_db):
    """场景1：第一轮要调工具，第二轮给最终答案"""
    fake_responses = [
        make_message(tool_calls=[make_tool_call("calculator", '{"expression": "1+1"}')]),
        make_message(content="1+1 等于 2"),
    ]
    with patch("app.agent.chat_with_tools", side_effect=fake_responses) as mock_chat:
        result = run_agent("请计算 1+1")

    assert result == "1+1 等于 2"
    assert mock_chat.call_count == 2   # 正好调了两次


def test_agent_reaches_max_iterations(tmp_db  ):
    """场景2：LLM 一直要调工具，达到上限返回兜底消息"""
    fake = make_message(tool_calls=[make_tool_call("calculator", '{"expression": "1+1"}')])
    with patch("app.agent.chat_with_tools", return_value=fake) as mock_chat:
        result = run_agent("请计算 1+1", max_iterations=3)
    assert result == "达到最大迭代次数，未能得到最终答案。"
    assert mock_chat.call_count == 3


def test_agent_loads_history(tmp_db):
    """历史被注入到传给 LLM 的 messages 里"""
    save_message("s1", "user", "历史问题")
    save_message("s1", "assistant", "历史回答")
    fake = make_message(content="最终答案")
    with patch("app.agent.chat_with_tools", return_value=fake) as mock_chat:
        result = run_agent("新问题", session_id="s1")
    assert result == "最终答案"
    sent = mock_chat.call_args[0][0]          # 拿到传给 LLM 的 messages
    roles = [m["role"] for m in sent]
    assert roles == ["system", "user", "assistant", "user"]  # system + 2 历史 + 新问题

def test_agent_saves_messages(tmp_db):
    """对话结束后，新的一对消息落库"""
    fake = make_message(content="最终答案")
    with patch("app.agent.chat_with_tools", return_value=fake):
        run_agent("新问题", session_id="s1")
    assert load_history("s1") == [
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "最终答案"},
    ]