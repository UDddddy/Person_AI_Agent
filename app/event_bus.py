"""阶段 5 · 事件总线 EventBus（第一课）。

发布-订阅（pub/sub）模型：
  - 发布方（如 ToolPipeline）：emit(event_type, data) 广播，不关心谁在听
  - 监听方（如扩展）：subscribe(event_type, handler) 订阅，不关心谁产生的
  双方通过"事件类型"解耦，谁都不用知道对方存在。
"""

# 事件类型常量（约定的"频道名"，发布/订阅共用同一拼写）
EVENT_SESSION_START = "session_start"
EVENT_AGENT_START = "agent_start"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_MESSAGE_UPDATE = "message_update"


class EventBus:
    def __init__(self):
        self._handlers = {}  # {event_type: [handler, ...]}

    def subscribe(self, event_type, handler):
        """注册一个监听器：event_type 事件发生时，调用 handler(data)。"""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def emit(self, event_type, data=None):
        """广播一个事件：通知所有订阅了该类型的监听器（没人订阅则安全跳过）。"""
        for handler in self._handlers.get(event_type, []):
            handler(data)
