"""阶段5 · EventBus / HookRegistry / ExtensionManager / 扩展 / 组装层 单元测试。

覆盖审查报告里的"阶段5零单测"缺口。每个测试只验证一个行为，
不依赖真实 LLM，全部离线可跑。
"""

import json

import pytest

from app.event_bus import (
    EventBus,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
)
from app.hooks import HookPoint, HookRegistry
from app.extensions.base import BaseExtension
from app.extensions.manager import ExtensionManager
from app.extensions.context import ExtensionContext
from app.extensions.observability import ObservabilityExtension
from app.extensions.approval import ApprovalExtension
from app.composition_root import create_pipeline
from tools.pipeline import ToolPipeline


# ===========================================================================
# EventBus
# ===========================================================================

class TestEventBus:
    def test_subscribe_and_emit_calls_handler(self):
        """订阅后 emit，handler 被调用且收到 data。"""
        bus = EventBus()
        received = []
        bus.subscribe(EVENT_TOOL_CALL, lambda data: received.append(data))
        bus.emit(EVENT_TOOL_CALL, {"name": "calculator"})
        assert received == [{"name": "calculator"}]

    def test_emit_with_no_subscribers_is_safe(self):
        """没人订阅的事件 emit 不崩（.get 兜底）。"""
        bus = EventBus()
        bus.emit("nonexistent_event", {"x": 1})  # 不应抛异常

    def test_multiple_subscribers_all_called(self):
        """同一事件多个监听器都被调用，按注册顺序。"""
        bus = EventBus()
        order = []
        bus.subscribe(EVENT_TOOL_CALL, lambda d: order.append("a"))
        bus.subscribe(EVENT_TOOL_CALL, lambda d: order.append("b"))
        bus.emit(EVENT_TOOL_CALL)
        assert order == ["a", "b"]

    def test_emit_data_default_none(self):
        """emit 不带 data 时，handler 收到 None。"""
        bus = EventBus()
        received = []
        bus.subscribe(EVENT_TOOL_RESULT, lambda data: received.append(data))
        bus.emit(EVENT_TOOL_RESULT)
        assert received == [None]


# ===========================================================================
# HookRegistry
# ===========================================================================

class TestHookRegistry:
    def test_emit_calls_all_hooks_ignores_return(self):
        """通知型 emit：所有钩子被调用，返回值被忽略。"""
        reg = HookRegistry()
        calls = []
        reg.register(HookPoint.AFTER_TOOL, lambda **kw: calls.append(("h1", kw)))
        reg.register(HookPoint.AFTER_TOOL, lambda **kw: calls.append(("h2", kw)))
        reg.emit(HookPoint.AFTER_TOOL, name="calculator", result="2")
        assert len(calls) == 2
        assert calls[0][1] == {"name": "calculator", "result": "2"}

    def test_intercept_first_non_none_short_circuits(self):
        """拦截型：第一个返回非 None 的钩子短路，后续钩子不调用。"""
        reg = HookRegistry()
        h2_called = []
        reg.register(HookPoint.BEFORE_TOOL, lambda **kw: None)          # 放行
        reg.register(HookPoint.BEFORE_TOOL, lambda **kw: "拦截!")        # 拦截
        reg.register(HookPoint.BEFORE_TOOL, lambda **kw: h2_called.append(1))  # 不应执行
        result = reg.intercept(HookPoint.BEFORE_TOOL, name="x", args={})
        assert result == "拦截!"
        assert h2_called == []  # 短路后第三个钩子没被调用

    def test_intercept_all_none_returns_none(self):
        """全部钩子返回 None → intercept 返回 None（放行）。"""
        reg = HookRegistry()
        reg.register(HookPoint.BEFORE_TOOL, lambda **kw: None)
        reg.register(HookPoint.BEFORE_TOOL, lambda **kw: None)
        assert reg.intercept(HookPoint.BEFORE_TOOL, name="x", args={}) is None

    def test_emit_unregistered_point_is_safe(self):
        """未注册点位 emit 不崩（.get 兜底）。"""
        reg = HookRegistry()
        reg.emit("nonexistent.point", x=1)

    def test_intercept_unregistered_point_returns_none(self):
        """未注册点位 intercept 返回 None（放行）。"""
        reg = HookRegistry()
        assert reg.intercept("nonexistent.point") is None


# ===========================================================================
# ExtensionManager
# ===========================================================================

class _FakeExtension(BaseExtension):
    """测试用假扩展：记录 load/setup 调用顺序和收到的 ctx。"""
    name = "fake"

    def __init__(self):
        self.calls = []

    def load(self):
        self.calls.append("load")

    def setup(self, ctx):
        self.calls.append(("setup", ctx))


class TestExtensionManager:
    def test_register_accepts_base_extension(self):
        mgr = ExtensionManager()
        ext = _FakeExtension()
        mgr.register(ext)
        assert ext in mgr.extensions

    def test_register_rejects_non_extension(self):
        """非 BaseExtension 注册抛 TypeError。"""
        mgr = ExtensionManager()
        with pytest.raises(TypeError):
            mgr.register("not_an_extension")

    def test_bind_all_calls_load_then_setup_with_ctx(self):
        """bind_all：每个扩展先 load() 再 setup(ctx)，顺序正确。"""
        mgr = ExtensionManager()
        ext = _FakeExtension()
        mgr.register(ext)
        ctx = ExtensionContext(bus="fake_bus", hooks="fake_hooks")
        mgr.bind_all(ctx)
        assert ext.calls[0] == "load"
        assert ext.calls[1] == ("setup", ctx)
        assert ext.calls[1][1].bus == "fake_bus"


# ===========================================================================
# ObservabilityExtension
# ===========================================================================

class TestObservabilityExtension:
    def test_setup_subscribes_to_bus(self):
        """setup 后，bus 上 5 个事件都有监听器。"""
        bus = EventBus()
        ext = ObservabilityExtension()
        ext.setup(ExtensionContext(bus=bus, hooks=None))
        # 5 个事件常量都应被订阅
        for event_type in ("session_start", "agent_start", "tool_call",
                           "tool_result", "message_update"):
            assert event_type in bus._handlers
            assert len(bus._handlers[event_type]) == 1

    def test_setup_with_none_bus_is_safe(self):
        """ctx.bus 为 None 时 setup 直接 return，不崩。"""
        ext = ObservabilityExtension()
        ext.setup(ExtensionContext(bus=None, hooks=None))  # 不应抛异常

    def test_emits_structured_json_log(self, capsys):
        """emit 事件后，print 出一行合法 JSON，含 ts/event/name。"""
        bus = EventBus()
        ObservabilityExtension().setup(ExtensionContext(bus=bus, hooks=None))
        bus.emit(EVENT_TOOL_CALL, {"name": "calculator", "args": {"expression": "1+1"}})
        out = capsys.readouterr().out.strip()
        record = json.loads(out)  # 必须是合法 JSON
        assert record["event"] == "tool_call"
        assert record["name"] == "calculator"
        assert "ts" in record


# ===========================================================================
# ApprovalExtension
# ===========================================================================

class TestApprovalExtension:
    def test_dangerous_tool_blocked_by_default(self):
        """默认 approver=None → 危险工具一律拦截，返回拒绝原因。"""
        ext = ApprovalExtension()
        result = ext._check_before_tool("run_command", {"command": "ls"})
        assert result is not None
        assert "危险操作" in result
        assert "审批未通过" in result

    def test_safe_tool_passes(self):
        """非危险工具返回 None（放行）。"""
        ext = ApprovalExtension()
        assert ext._check_before_tool("calculator", {"expression": "1+1"}) is None

    def test_approver_allows_dangerous_tool(self):
        """注入 approver 返回 True → 危险工具放行（返回 None）。"""
        ext = ApprovalExtension(approver=lambda name, args: True)
        assert ext._check_before_tool("run_command", {"command": "ls"}) is None

    def test_approver_denies_dangerous_tool(self):
        """注入 approver 返回 False → 拦截。"""
        ext = ApprovalExtension(approver=lambda name, args: False)
        result = ext._check_before_tool("run_command", {"command": "ls"})
        assert result is not None

    def test_custom_dangerous_tools_list(self):
        """自定义危险名单：calculator 也被拦。"""
        ext = ApprovalExtension(dangerous_tools=["calculator"])
        assert ext._check_before_tool("calculator", {"expression": "1+1"}) is not None
        assert ext._check_before_tool("run_command", {"command": "ls"}) is None  # 不在名单

    def test_setup_registers_before_tool_hook(self):
        """setup 后，hooks 注册表的 BEFORE_TOOL 点位有钩子。"""
        hooks = HookRegistry()
        ApprovalExtension().setup(ExtensionContext(bus=None, hooks=hooks))
        assert HookPoint.BEFORE_TOOL in hooks._hooks
        assert len(hooks._hooks[HookPoint.BEFORE_TOOL]) == 1


# ===========================================================================
# composition_root（组装层）
# ===========================================================================

class TestCompositionRoot:
    def test_create_pipeline_returns_tool_pipeline(self):
        p = create_pipeline()
        assert isinstance(p, ToolPipeline)

    def test_create_pipeline_has_bus_and_hooks(self):
        """组装层返回的 pipeline 带有 bus 和 hooks（扩展已接入）。"""
        p = create_pipeline()
        assert p.bus is not None
        assert p.hooks is not None

    def test_create_pipeline_approval_active(self):
        """端到端：组装好的 pipeline 上，run_command 被拦、calculator 放行。"""
        p = create_pipeline()
        blocked = p.execute("run_command", {"command": "ls"})
        assert "审批未通过" in blocked
        assert p.execute("calculator", {"expression": "1+1"}) == "2"

    def test_create_pipeline_observability_emits(self, capsys):
        """组装好的 pipeline 执行工具时，observability 打出结构化日志。"""
        p = create_pipeline()
        p.execute("calculator", {"expression": "1+1"})
        out = capsys.readouterr().out
        lines = [json.loads(line) for line in out.strip().split("\n") if line.strip()]
        events = [r["event"] for r in lines]
        assert "tool_call" in events
        assert "tool_result" in events
