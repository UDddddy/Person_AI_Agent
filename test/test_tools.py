from tools.executor import execute_tool

def test_calculator():
    result = execute_tool("calculator", '{"expression": "123 + 456"}')
    assert result == "579"   # 注意：execute 返回的是字符串