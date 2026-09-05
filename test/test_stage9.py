"""阶段9 · 生产化 单元测试。

覆盖：MetricsCollector 指标收集、EvalRunner 评估框架。
"""

import pytest

from app.event_bus import EventBus, EVENT_TOOL_CALL, EVENT_TOOL_RESULT, EVENT_LLM_RESULT
from app.metrics import MetricsCollector
from app.evaluation import EvalRunner, EvalCase
from app.providers.base import LLMProvider, LLMResponse


# ===========================================================================
# FakeProvider
# ===========================================================================

class ScriptedProvider(LLMProvider):
    @property
    def model_name(self):
        return "scripted"

    def chat(self, messages, tools=None):
        user_input = messages[-1]["content"]
        if "计算" in user_input:
            return LLMResponse(
                content="", tool_calls=[{"name": "calculator", "args": {}, "id": "t1"}],
                usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            )
        return LLMResponse(
            content="你好，我是AI助手",
            usage={"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
        )

    def stream(self, messages, tools=None):
        yield LLMResponse(content="x")


# ===========================================================================
# MetricsCollector
# ===========================================================================

class TestMetricsCollector:
    def test_tool_metrics(self):
        bus = EventBus()
        m = MetricsCollector()
        m.subscribe_bus(bus)

        bus.emit(EVENT_TOOL_CALL, {"name": "calculator"})
        bus.emit(EVENT_TOOL_RESULT, {"name": "calculator", "latency": 0.01})
        bus.emit(EVENT_TOOL_CALL, {"name": "calculator"})
        bus.emit(EVENT_TOOL_RESULT, {"name": "calculator", "latency": 0.03})

        r = m.report()
        assert r["tool"]["total_calls"] == 2
        assert r["tool"]["by_name"] == {"calculator": 2}
        assert r["tool"]["avg_latency_s"] == 0.02
        assert r["tool"]["max_latency_s"] == 0.03

    def test_llm_metrics(self):
        bus = EventBus()
        m = MetricsCollector()
        m.subscribe_bus(bus)

        bus.emit(EVENT_LLM_RESULT, {
            "model": "deepseek", "latency": 1.0,
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "has_tool_calls": True,
        })
        bus.emit(EVENT_LLM_RESULT, {
            "model": "deepseek", "latency": 0.5,
            "usage": {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230},
            "has_tool_calls": False,
        })

        r = m.report()
        assert r["llm"]["total_calls"] == 2
        assert r["llm"]["with_tool_calls"] == 1
        assert r["llm"]["avg_latency_s"] == 0.75
        assert r["llm"]["max_latency_s"] == 1.0
        assert r["token_usage"]["total_tokens"] == 380
        assert r["token_usage"]["prompt_tokens"] == 300

    def test_empty_metrics(self):
        m = MetricsCollector()
        r = m.report()
        assert r["tool"]["total_calls"] == 0
        assert r["llm"]["total_calls"] == 0
        assert r["tool"]["avg_latency_s"] == 0.0
        assert r["token_usage"]["total_tokens"] == 0

    def test_reset(self):
        bus = EventBus()
        m = MetricsCollector()
        m.subscribe_bus(bus)
        bus.emit(EVENT_TOOL_CALL, {"name": "x"})
        assert m.report()["tool"]["total_calls"] == 1
        m.reset()
        assert m.report()["tool"]["total_calls"] == 0

    def test_subscribe_multiple_buses(self):
        """同一个 MetricsCollector 可以订阅多个 EventBus。"""
        bus1 = EventBus()
        bus2 = EventBus()
        m = MetricsCollector()
        m.subscribe_bus(bus1)
        m.subscribe_bus(bus2)

        bus1.emit(EVENT_TOOL_CALL, {"name": "a"})
        bus2.emit(EVENT_TOOL_CALL, {"name": "b"})
        assert m.report()["tool"]["total_calls"] == 2

    def test_tool_result_without_latency(self):
        """tool_result 事件不带 latency 时不崩溃。"""
        bus = EventBus()
        m = MetricsCollector()
        m.subscribe_bus(bus)
        bus.emit(EVENT_TOOL_RESULT, {"name": "x", "result": "ok"})  # 无 latency
        assert m.report()["tool"]["avg_latency_s"] == 0.0

    def test_llm_result_without_usage(self):
        """LLM 结果不带 usage 时不崩溃。"""
        bus = EventBus()
        m = MetricsCollector()
        m.subscribe_bus(bus)
        bus.emit(EVENT_LLM_RESULT, {"model": "x", "latency": 0.1})  # 无 usage
        assert m.report()["token_usage"]["total_tokens"] == 0


# ===========================================================================
# EvalRunner
# ===========================================================================

class TestEvalRunner:
    def test_tool_correct(self):
        runner = EvalRunner(ScriptedProvider())
        case = EvalCase(name="计算", user_input="帮我计算 1+1", expected_tool="calculator")
        r = runner.run_case(case)
        assert r.tool_correct
        assert r.actual_tool == "calculator"
        assert r.passed

    def test_no_tool_expected(self):
        runner = EvalRunner(ScriptedProvider())
        case = EvalCase(name="对话", user_input="你好", expected_tool="")
        r = runner.run_case(case)
        assert r.tool_correct
        assert r.actual_tool == ""

    def test_tool_incorrect(self):
        runner = EvalRunner(ScriptedProvider())
        case = EvalCase(name="选错", user_input="帮我计算 1+1", expected_tool="get_current_time")
        r = runner.run_case(case)
        assert not r.tool_correct
        assert not r.passed

    def test_keywords_match(self):
        runner = EvalRunner(ScriptedProvider())
        case = EvalCase(name="对话", user_input="你好", expected_tool="",
                         expected_keywords=["AI助手"])
        r = runner.run_case(case)
        assert r.keywords_matched == ["AI助手"]
        assert r.keywords_missing == []
        assert r.passed

    def test_keywords_missing(self):
        runner = EvalRunner(ScriptedProvider())
        case = EvalCase(name="对话", user_input="你好", expected_tool="",
                         expected_keywords=["不存在的词"])
        r = runner.run_case(case)
        assert r.keywords_missing == ["不存在的词"]
        assert not r.passed

    def test_run_all_summary(self):
        runner = EvalRunner(ScriptedProvider())
        cases = [
            EvalCase(name="计算", user_input="帮我计算", expected_tool="calculator"),
            EvalCase(name="对话", user_input="你好", expected_tool=""),
            EvalCase(name="选错", user_input="帮我计算", expected_tool="get_current_time"),
        ]
        summary = runner.run_all(cases)
        assert summary.total == 3
        assert summary.passed == 2  # 计算✓ 对话✓ 选错✗
        assert summary.success_rate == pytest.approx(2 / 3)
        assert summary.tool_accuracy == pytest.approx(2 / 3)
        assert summary.total_tokens == 60 + 35 + 60  # 155

    def test_empty_cases(self):
        runner = EvalRunner(ScriptedProvider())
        summary = runner.run_all([])
        assert summary.total == 0
        assert summary.passed == 0
        assert summary.success_rate == 0
        assert summary.avg_latency_s == 0

    def test_result_records_latency(self):
        runner = EvalRunner(ScriptedProvider())
        r = runner.run_case(EvalCase(name="x", user_input="你好", expected_tool=""))
        assert r.latency_s >= 0
