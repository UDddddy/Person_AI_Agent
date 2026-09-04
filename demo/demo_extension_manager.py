

from app.event_bus import EventBus,EVENT_AGENT_START
from app.extensions.manager import ExtensionManager
from app.extensions.observability import ObservabilityExtension
from tools.pipeline import ToolPipeline
def main():
    manager = ExtensionManager()
    
    manager.register(ObservabilityExtension())
    bus = EventBus()

    print("=== ① 绑定前 emit（预期：下面没有 JSON 日志）===")
    bus.emit(EVENT_AGENT_START)
    print("=== ② bind_all 绑定（通电）===")
    manager.bind_all(bus)
    print("=== ③ 绑定后执行工具（预期：tool_call / tool_result 两条日志）===")
    result = ToolPipeline(bus=bus).execute("calculator", {"expression": "1+1"})
    print("工具返回:", result)

if __name__ == "__main__":
    main()
    