from app.llm import chat_with_tools

print("开始执行 test_tools_call.py")
message = "帮我计算1+1"
response = chat_with_tools(message)
print(response)
print("工具调用结果")
for tool_call in response.tool_calls:
    print(tool_call.function.name)
    print(tool_call.function.arguments)