import json
from pathlib import Path

from tools.calculator import CalculatorTool
from tools.base import BaseTool
from tools.time_tool import GetCurrentTimeTool
from tools.run_command import RunCommandTool
from tools.write_file import WriteFileTool

from tools.read_file import ReadFileTool
from tools.edit_file import EditFileTool
from tools.hash_text import HashTextTool
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
register_tool(EditFileTool())
register_tool(HashTextTool())   # 自定义工具示例：文本哈希（接入教程见 tools/hash_text.py 模块注释）

# ---------------------------------------------------------------------------
# MCP 外部工具接入（可选，默认关闭）
# 三步启用：
#   1. pip install mcp        （官方 SDK；Windows 沙箱记得加 --no-cache-dir）
#   2. 复制 mcp_servers.example.json 为 mcp_servers.json，改成你的服务器配置
#   3. 重启应用 —— 下方代码会自动把所有 MCP 工具注册进 Tools / TOOL_SCHEMA
# 双重保护：没有配置文件不动；有配置但没装 mcp 包也只是提示跳过，不影响启动。
# ---------------------------------------------------------------------------
_MCP_CFG = Path(__file__).resolve().parent / "mcp_servers.json"
if _MCP_CFG.exists():
    try:
        from tools.mcp_bridge import register_mcp_servers
        register_mcp_servers(json.loads(_MCP_CFG.read_text(encoding="utf-8")))
    except ImportError:
        print("[registry] 检测到 mcp_servers.json 但未安装 mcp 包，"
              "跳过 MCP 工具注册（pip install mcp 后重启即可生效）")
    except Exception as e:
        print(f"[registry] MCP 工具注册失败（不影响其他工具）：{e}")
