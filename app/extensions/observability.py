"""阶段 5 · 第二课：observability 扩展（结构化日志）。

通过订阅 EventBus 事件，把 Agent 的每次动作输出成一行结构化 JSON 日志。
业务模块（如 ToolPipeline）完全不知道本模块存在 —— 这就是"横切关注点"。
"""

import json                                  # 把 dict 序列化成 JSON 字符串
from datetime import datetime                # 生成每条日志的时间戳

from app.event_bus import (                  # 从第一课的事件总线导入 5 个"频道名"常量
    EVENT_SESSION_START,
    EVENT_AGENT_START,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_MESSAGE_UPDATE,
)
from .base import BaseExtension

class ObservabilityExtension(BaseExtension):
    """把结构化日志监听器注册到总线上。"""
    name = "observability"
    def _log_event(self,event_type, data):
        """把一个事件格式化成一行 JSON 并打印（结构化日志的最小实现）。

        事件总线回调的统一签名是 handler(data)；这里额外带上 event_type，
        所以下面用 lambda 把"频道名"一起传进来。
        """
        record = {                                              # 一条日志 = 一个 dict
            "ts": datetime.now().isoformat(timespec="seconds"), # 时间戳（秒级，ISO 格式）
            "event": event_type,                                # 事件类型：tool_call / tool_result ...
        }
        if data:                                                # data 可能是 None（emit 时没带数据）
            record.update(data)                                 # 把 {name/args} 或 {name/result} 平铺进来
        print(json.dumps(record, ensure_ascii=False))           # 序列化成一行 JSON；False=中文不转义成 \uXXXX
    def setup(self,bus):
        """把结构化日志监听器注册到总线上；返回 bus 便于链式调用。

        调用一次，之后总线上任何被订阅的事件都会自动变成一行结构化日志。
        """
        for event_type in (                                     # 遍历要订阅的全部事件类型
            EVENT_SESSION_START,
            EVENT_AGENT_START,
            EVENT_TOOL_CALL,
            EVENT_TOOL_RESULT,
            EVENT_MESSAGE_UPDATE,
        ):
            # 关键：t=event_type 是"默认参数"，在循环这一圈当场把值固定下来。
            # 如果写成 lambda data: _log_event(event_type, data)，闭包会延迟取值，
            # 等事件真正触发时循环早结束，5 个监听器会全用最后一个事件类型。
            bus.subscribe(event_type, lambda data, t=event_type: self._log_event(t, data))
