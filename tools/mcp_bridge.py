"""MCP（Model Context Protocol）桥接器：把外部 MCP Server 的工具接入本项目。

为什么能无痛桥接（本文件的核心设计依据）：
- 本项目 TOOL_SCHEMA 是 OpenAI function-calling 格式，parameters 就是 JSON Schema；
- MCP 工具的 inputSchema 同样是 JSON Schema —— 两边天然同构，字段直接映射；
- 接入后走同一个 ToolPipeline：审计日志、事件总线、BEFORE_TOOL 审批钩子全部生效。

依赖：pip install mcp   （官方 Python SDK；Windows 沙箱记得加 --no-cache-dir）

用法（三步）：
1. 在 tools/mcp_servers.json 放配置（格式见同目录 mcp_servers.example.json）；
2. pip install mcp；
3. 重启应用 —— registry.py 检测到配置文件后自动调 register_mcp_servers()，
   所有 MCP 工具出现在 TOOL_SCHEMA 里，LLM 即可自主调用。

实现说明：
- 每次工具调用新建一条 MCP 会话（connect → initialize → call → close），
  学习项目下最简单可靠；生产环境建议把 ClientSession 缓存为长连并处理重连。
- 传输层支持 stdio（本地子进程）和 streamable http（远程服务）两种。
"""

import asyncio
import json
from pathlib import Path

from pydantic import create_model

from tools.base import BaseTool


# ---------------------------------------------------------------------------
# 映射层：MCP 工具定义 → BaseTool（纯函数，可离线测试，不依赖 mcp 包）
# ---------------------------------------------------------------------------

def mcp_tool_to_base_tool(tool_def: dict, remote_call, server_name: str) -> BaseTool:
    """把一个 MCP 工具定义包装成本项目 BaseTool 实例。

    Args:
        tool_def: MCP tools/list 返回的单个工具定义，
                  形如 {"name": ..., "description": ..., "inputSchema": {...}}。
        remote_call: 回调 fn(tool_name, arguments) -> str，负责真正发起 MCP 调用
                  （由连接层注入，桥接层不关心传输细节）。
        server_name: 来源服务器名，写进 description 前缀方便排查。

    用 pydantic 官方的 create_model 动态生成 BaseTool 子类（v2 下比 type() 稳，
    字段默认值显式传递），run 闭包持有 tool_name 实现同名函数多实例。
    """
    tool_name = tool_def["name"]
    description = f"[MCP:{server_name}] {tool_def.get('description') or tool_name}"
    input_schema = tool_def.get("inputSchema") or {"type": "object", "properties": {}}

    def run(self, **kwargs):
        return remote_call(tool_name, kwargs)

    cls = create_model(
        f"Mcp_{tool_name}",
        __base__=BaseTool,
        name=(str, tool_name),
        description=(str, description),
        parameters=(dict, input_schema),
    )
    cls.run = run
    return cls()


# ---------------------------------------------------------------------------
# 连接层：MCP 会话管理（依赖 mcp 包，延迟导入）
# ---------------------------------------------------------------------------

def _require_mcp():
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "使用 MCP 桥接需要先安装官方 SDK：pip install mcp"
        ) from exc
    from mcp import ClientSession
    return ClientSession


async def _open_session(cfg: dict):
    """按配置建立 MCP 会话，返回 (read_stream, write_stream, session) 上下文组合。

    用 asynccontextmanager 嵌套的写法把三层 with 合并成一个异步上下文，
    调用方 async with _open_session(cfg) as session 即可。
    """
    from contextlib import AsyncExitStack

    ClientSession = _require_mcp()
    transport = cfg.get("transport", "stdio")
    stack = AsyncExitStack()

    if transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env") or None,
        )
        read, write = await stack.enter_async_context(stdio_client(params))
    elif transport in ("http", "streamable_http"):
        from mcp.client.streamable_http import streamablehttp_client
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(cfg["url"])
        )
    else:
        await stack.aclose()
        raise ValueError(f"不支持的 MCP transport: {transport}（支持 stdio / http）")

    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return stack, session


def _extract_text(result) -> str:
    """从 MCP CallToolResult 提取纯文本（content 是 TextContent 等对象的列表）。"""
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    if getattr(result, "isError", False):
        return "[MCP 工具报错] " + "\n".join(parts)
    return "\n".join(parts) or "(空结果)"


async def _list_tools_remote(cfg: dict) -> list[dict]:
    """连接 MCP Server 拉取工具清单 → [{"name","description","inputSchema"}]。"""
    stack, session = await _open_session(cfg)
    try:
        result = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema or {},
            }
            for t in result.tools
        ]
    finally:
        await stack.aclose()


async def _call_tool_remote(cfg: dict, tool_name: str, arguments: dict) -> str:
    """连接 MCP Server 执行一次工具调用，返回文本结果。"""
    stack, session = await _open_session(cfg)
    try:
        result = await session.call_tool(tool_name, arguments=arguments)
        return _extract_text(result)
    finally:
        await stack.aclose()


# ---------------------------------------------------------------------------
# 注册入口：被 tools/registry.py 在 import 时调用
# ---------------------------------------------------------------------------

def register_mcp_servers(config: dict) -> list[str]:
    """注册配置里所有 MCP Server 的工具，返回成功注册的工具名列表。

    配置格式（与主流 MCP 客户端的 mcp.json 习惯一致）：
    {"mcpServers": {"<server名>": {"transport": "stdio", "command": ..., "args": [...]}}}
    """
    ClientSession = _require_mcp()  # 先确保 SDK 已安装，失败直接抛给调用方处理
    registered = []

    for server_name, cfg in (config.get("mcpServers") or {}).items():
        try:
            tool_defs = asyncio.run(_list_tools_remote(cfg))
        except Exception as exc:
            print(f"[mcp_bridge] 连接 {server_name} 失败，跳过：{exc}")
            continue

        # 闭包绑定当前 server 配置：每个 MCP 工具的 run 都走"新建会话→调用→关闭"
        def make_remote_call(server_cfg: dict):
            def remote_call(tool_name: str, args: dict) -> str:
                return asyncio.run(_call_tool_remote(server_cfg, tool_name, args))
            return remote_call

        remote_call = make_remote_call(cfg)
        for tool_def in tool_defs:
            from tools.registry import register_tool  # 局部导入避免循环依赖
            register_tool(mcp_tool_to_base_tool(tool_def, remote_call, server_name))
            registered.append(tool_def["name"])

        print(f"[mcp_bridge] 已接入 {server_name} 的 {len(tool_defs)} 个工具: "
              f"{[d['name'] for d in tool_defs]}")

    return registered


def register_mcp_servers_from_file() -> list[str]:
    """从 tools/mcp_servers.json 读配置并注册（供手动/调试调用）。"""
    cfg_path = Path(__file__).resolve().parent / "mcp_servers.json"
    if not cfg_path.exists():
        return []
    return register_mcp_servers(json.loads(cfg_path.read_text(encoding="utf-8")))
