"""阶段 5 · observability 扩展（结构化日志，通知型扩展示范）。

通过订阅 ctx.bus 上的事件，把 Agent 的每次动作输出成一行结构化 JSON 日志。
业务模块（如 ToolPipeline）完全不知道本模块存在 —— 这就是"横切关注点"。
"""

import json
from datetime import datetime

from app.event_bus import (
    EVENT_SESSION_START,
    EVENT_AGENT_START,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_MESSAGE_UPDATE,
)
from .base import BaseExtension


class ObservabilityExtension(BaseExtension):
    name = "observability"

    def _log_event(self, event_type, data):
        """把一个事件格式化成一行 JSON 并打印。"""
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event_type,
        }
        if data:  # data 可能是 None
            record.update(data)
        print(json.dumps(record, ensure_ascii=False))

    def setup(self, ctx):
        """绑定阶段：从上下文取总线，订阅全部事件。"""
        bus = ctx.bus
        if bus is None:
            return
        for event_type in (
            EVENT_SESSION_START,
            EVENT_AGENT_START,
            EVENT_TOOL_CALL,
            EVENT_TOOL_RESULT,
            EVENT_MESSAGE_UPDATE,
        ):
            # t=event_type 默认参数在循环当圈固定，避免闭包延迟取值（5 个监听者都用最后一个类型）
            bus.subscribe(event_type, lambda data, t=event_type: self._log_event(t, data))
