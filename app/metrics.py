"""阶段9 · 课1：指标收集器 MetricsCollector。

订阅 EventBus 事件，统计运行时指标：
- 工具调用次数、按工具名分布、延迟（平均/最大）
- LLM 调用次数、带 tool_calls 的比例、延迟（平均/最大）
- Token 用量（prompt/completion/total）

和 ObservabilityExtension 的区别：
- ObservabilityExtension：每条事件打印一行结构化 JSON 日志（实时流水）
- MetricsCollector：聚合统计，最终输出一份指标报告（汇总视图）
"""

from app.event_bus import (
    EventBus,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_LLM_RESULT,
)


def _avg(values):
    return sum(values) / len(values) if values else 0.0


def _max(values):
    return max(values) if values else 0.0


class MetricsCollector:
    """聚合事件指标，输出汇总报告。"""

    def __init__(self):
        # 工具指标
        self.tool_calls = 0
        self.tool_by_name: dict[str, int] = {}
        self.tool_latencies: list[float] = []

        # LLM 指标
        self.llm_calls = 0
        self.llm_with_tool_calls = 0
        self.llm_latencies: list[float] = []

        # Token 用量
        self.token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def subscribe_bus(self, bus: EventBus):
        """订阅一个 EventBus 的相关事件。可多次调用，订阅多个 bus。"""
        bus.subscribe(EVENT_TOOL_CALL, self._on_tool_call)
        bus.subscribe(EVENT_TOOL_RESULT, self._on_tool_result)
        bus.subscribe(EVENT_LLM_RESULT, self._on_llm_result)

    # ---- 事件处理 ----

    def _on_tool_call(self, data):
        self.tool_calls += 1
        name = (data or {}).get("name", "unknown")
        self.tool_by_name[name] = self.tool_by_name.get(name, 0) + 1

    def _on_tool_result(self, data):
        latency = (data or {}).get("latency")
        if latency is not None:
            self.tool_latencies.append(latency)

    def _on_llm_result(self, data):
        data = data or {}
        self.llm_calls += 1

        latency = data.get("latency")
        if latency is not None:
            self.llm_latencies.append(latency)

        if data.get("has_tool_calls"):
            self.llm_with_tool_calls += 1

        usage = data.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.token_usage[key] += usage.get(key, 0) or 0

    # ---- 报告 ----

    def report(self) -> dict:
        """输出汇总指标报告。"""
        return {
            "tool": {
                "total_calls": self.tool_calls,
                "by_name": dict(self.tool_by_name),
                "avg_latency_s": round(_avg(self.tool_latencies), 4),
                "max_latency_s": round(_max(self.tool_latencies), 4),
            },
            "llm": {
                "total_calls": self.llm_calls,
                "with_tool_calls": self.llm_with_tool_calls,
                "avg_latency_s": round(_avg(self.llm_latencies), 4),
                "max_latency_s": round(_max(self.llm_latencies), 4),
            },
            "token_usage": dict(self.token_usage),
        }

    def reset(self):
        """清零所有指标。"""
        self.__init__()
