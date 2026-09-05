"""阶段 5 · 课3验证：扩展两阶段加载（加载=登记 / 绑定=注入运行时）。

从项目根运行：python -m demo.demo_extension_manager
"""

from app.event_bus import EventBus, EVENT_AGENT_START
from app.extensions.context import ExtensionContext
from app.extensions.manager import ExtensionManager
from app.extensions.observability import ObservabilityExtension
from tools.pipeline import ToolPipeline


def main():
    manager = ExtensionManager()
    manager.register(ObservabilityExtension())   # 加载阶段：只登记
    bus = EventBus()
    ctx = ExtensionContext(bus=bus)              # 组装运行时上下文

    print("=== ① 绑定前 emit（预期：下面没有 JSON 日志）===")
    bus.emit(EVENT_AGENT_START)

    print("=== ② bind_all(ctx) 绑定（通电）===")
    manager.bind_all(ctx)

    print("=== ③ 绑定后执行工具（预期：tool_call / tool_result 两条日志）===")
    result = ToolPipeline(bus=bus).execute("calculator", {"expression": "1+1"})
    print("工具返回:", result)


if __name__ == "__main__":
    main()
