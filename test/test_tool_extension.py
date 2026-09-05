"""工具扩展机制测试：自定义工具注册 + MCP 桥接映射。

覆盖：
1. 自定义工具（hash_text）三步接入后：出现在注册表与 TOOL_SCHEMA、
   经 ToolPipeline 正常执行、必填参数校验生效
2. MCP 桥接的纯映射函数 mcp_tool_to_base_tool：name/description/inputSchema
   直接映射、run 走注入的 remote_call（不依赖 mcp 包，离线可测）
"""

from tools.hash_text import HashTextTool
from tools.mcp_bridge import mcp_tool_to_base_tool
from tools.pipeline import ToolPipeline
from tools.registry import TOOL_SCHEMA, Tools


# ---------------------------------------------------------------------------
# 路径1：自定义工具接入
# ---------------------------------------------------------------------------

def test_hash_text_registered():
    """registry import 时自动注册：注册表和 TOOL_SCHEMA 都能看到。"""
    assert isinstance(Tools["hash_text"], HashTextTool)
    schema_names = [s["function"]["name"] for s in TOOL_SCHEMA]
    assert "hash_text" in schema_names


def test_hash_text_via_pipeline():
    """走 ToolPipeline 执行：正常计算 + 必填参数校验。"""
    pipeline = ToolPipeline()  # 不挂 bus/hooks，测工具本身
    result = pipeline.execute("hash_text", {"text": "hello", "algorithm": "md5"})
    assert result == "md5: 5d41402abc4b2a76b9719d911017c592"

    # 缺必填参数 → pipeline 前置校验拦截
    assert pipeline.execute("hash_text", {"algorithm": "md5"}) == "参数不完整"

    # 默认算法 sha256
    default = pipeline.execute("hash_text", {"text": "hello"})
    assert default.startswith("sha256: ")


# ---------------------------------------------------------------------------
# 路径3：MCP 桥接映射层
# ---------------------------------------------------------------------------

def test_mcp_tool_mapping():
    """MCP 工具定义 → BaseTool：字段直接映射，run 走注入的 remote_call。"""
    tool_def = {
        "name": "mcp_weather",
        "description": "查询城市天气",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    }
    calls = []

    def fake_remote_call(tool_name, args):
        calls.append((tool_name, args))
        return "晴 25°C"

    tool = mcp_tool_to_base_tool(tool_def, fake_remote_call, server_name="demo")

    # 映射正确性
    assert tool.name == "mcp_weather"
    assert tool.description == "[MCP:demo] 查询城市天气"
    assert tool.parameters == tool_def["inputSchema"]  # JSON Schema 天然同构

    # 执行走注入的回调，结果原样返回
    assert tool.execute({"city": "北京"}) == "晴 25°C"
    assert calls == [("mcp_weather", {"city": "北京"})]

    # 映射出的工具可被注册（与普通工具同等待遇）
    from tools.registry import register_tool
    register_tool(tool)
    assert Tools["mcp_weather"] is tool
    assert "mcp_weather" in [s["function"]["name"] for s in TOOL_SCHEMA]
