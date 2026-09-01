
from tools.registry import Tools

def execute_tool(tool_name,arguments):
    try:
       tool = Tools.get(tool_name)
       if tool is None:
        return f"找不到工具 {tool_name}"
       return tool.execute(arguments)
    except Exception as e:
        return f"执行工具时出错: {e}"
