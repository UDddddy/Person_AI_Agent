from app.llm import chat_with_tools
from tools.executor import execute_calculator


message = "帮我计算1+1"

response = chat_with_tools(message)
print(response.tool_calls)
for tool_call in response.tool_calls:

    result = execute_calculator(tool_call)

    print("工具名称：", tool_call.function.name)
    print("工具参数：", tool_call.function.arguments)
    print("工具执行结果：", result)