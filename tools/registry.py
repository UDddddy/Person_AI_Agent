from tools.calculator import CalculatorTool
from tools.base import BaseTool
from tools.time_tool import GetCurrentTimeTool
from tools.run_command import RunCommandTool
from tools.write_file import WriteFileTool

from tools.read_file import ReadFileTool
Tools = {}
TOOL_SCHEMA = []

def register_tool(tool:BaseTool):
    Tools[tool.name] = tool
    TOOL_SCHEMA.append(tool.schema())

register_tool(CalculatorTool())
register_tool(GetCurrentTimeTool())
register_tool(RunCommandTool())
register_tool(WriteFileTool())
register_tool(ReadFileTool())