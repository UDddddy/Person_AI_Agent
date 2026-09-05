"""阶段9 · 端到端演示：串联项目核心能力。

用 FakeProvider 不发网络请求，展示：
1. AgentChain 顺序流水线（求职助手）
2. AgentTeam 动态调度
3. Subagent 后台并行
4. MetricsCollector 指标收集
5. EvalRunner 评估框架

运行：python -m demo.demo_stage9_e2e
"""

import time

from app.orchestration import (
    AgentChain, ChainStep,
    AgentTeam, SubAgent,
    SubagentRunner, SubagentSpec,
    JobAssistant,
)
from app.metrics import MetricsCollector
from app.evaluation import EvalRunner, EvalCase
from app.event_bus import EventBus, EVENT_TOOL_CALL, EVENT_TOOL_RESULT, EVENT_LLM_RESULT
from app.providers.base import LLMProvider, LLMResponse


class FakeProvider(LLMProvider):
    """按顺序返回预设回复的 FakeProvider。"""
    def __init__(self, replies):
        self.replies = replies
        self._idx = 0

    @property
    def model_name(self):
        return "fake-demo"

    def chat(self, messages, tools=None):
        reply = self.replies[self._idx] if self._idx < len(self.replies) else "默认回复"
        self._idx += 1
        if isinstance(reply, dict):
            return LLMResponse(**reply)
        return LLMResponse(content=reply)

    def stream(self, messages, tools=None):
        yield LLMResponse(content="x")


class SlowFakeProvider(LLMProvider):
    def __init__(self, delay=0.05):
        self.delay = delay

    @property
    def model_name(self):
        return "slow-fake"

    def chat(self, messages, tools=None):
        time.sleep(self.delay)
        return LLMResponse(content=f"完成: {messages[-1]['content'][:15]}")

    def stream(self, messages, tools=None):
        yield LLMResponse(content="x")


def demo_chain():
    print("\n" + "=" * 60)
    print("演示1：AgentChain 顺序流水线（求职助手）")
    print("=" * 60)
    assistant = JobAssistant(FakeProvider([
        "【planner】必备技能：Python/LLM/Agent；经验：3年+",
        "【builder】匹配度80%；简历建议：突出Agent项目",
        "【reviewer】评分8/10；优化：补充Docker经验",
    ]))
    result = assistant.run(
        jd="招聘AI Agent工程师，要求Python、LangGraph、LLM应用经验",
        skills="Python, LangChain, 2年LLM经验",
    )
    for step in result.steps:
        print(f"  [{step.name}] {step.output[:50]}...")
    print(f"  最终输出: {result.final_output[:60]}...")


def demo_team():
    print("\n" + "=" * 60)
    print("演示2：AgentTeam 动态调度")
    print("=" * 60)
    agents = [
        SubAgent(name="planner", description="分析需求、拆解任务", system_prompt="你是规划师"),
        SubAgent(name="builder", description="生成内容、编写代码", system_prompt="你是构建者"),
        SubAgent(name="reviewer", description="复核质量、检查问题", system_prompt="你是审核者"),
    ]
    team = AgentTeam(FakeProvider(["planner", "规划结果：分3步完成"]), agents)
    r = team.dispatch("分析这个需求")
    print(f"  任务: 分析这个需求")
    print(f"  调度器选择: {r.selected_agent}")
    print(f"  执行结果: {r.result}")


def demo_subagent():
    print("\n" + "=" * 60)
    print("演示3：Subagent 后台并行")
    print("=" * 60)
    bus = EventBus()
    done = []
    bus.subscribe("subagent.done", lambda d: done.append(d["name"]))

    specs = [
        SubagentSpec(name="查天气", system_prompt="天气查询", task="北京天气"),
        SubagentSpec(name="查新闻", system_prompt="新闻检索", task="AI新闻"),
        SubagentSpec(name="查股价", system_prompt="金融数据", task="AAPL股价"),
    ]
    runner = SubagentRunner(SlowFakeProvider(delay=0.05), bus, max_workers=3)
    start = time.time()
    results = runner.run_parallel(specs)
    elapsed = time.time() - start

    for r in results:
        print(f"  [{r.name}] success={r.success} result={r.result}")
    print(f"  并行耗时: {elapsed:.2f}s（串行需0.15s）")
    print(f"  完成事件: {done}")


def demo_metrics():
    print("\n" + "=" * 60)
    print("演示4：MetricsCollector 指标收集")
    print("=" * 60)
    bus = EventBus()
    metrics = MetricsCollector()
    metrics.subscribe_bus(bus)

    # 模拟运行时事件
    bus.emit(EVENT_TOOL_CALL, {"name": "calculator", "args": {"x": 1}})
    bus.emit(EVENT_TOOL_RESULT, {"name": "calculator", "result": "2", "latency": 0.012})
    bus.emit(EVENT_LLM_RESULT, {
        "model": "deepseek-chat", "latency": 1.2,
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        "has_tool_calls": True,
    })

    report = metrics.report()
    print(f"  工具调用: {report['tool']['total_calls']} 次")
    print(f"  工具分布: {report['tool']['by_name']}")
    print(f"  工具平均延迟: {report['tool']['avg_latency_s']}s")
    print(f"  LLM调用: {report['llm']['total_calls']} 次 (含工具调用 {report['llm']['with_tool_calls']} 次)")
    print(f"  LLM平均延迟: {report['llm']['avg_latency_s']}s")
    print(f"  Token用量: {report['token_usage']}")


def demo_evaluation():
    print("\n" + "=" * 60)
    print("演示5：EvalRunner 评估框架")
    print("=" * 60)

    class EvalProvider(LLMProvider):
        @property
        def model_name(self):
            return "eval-fake"

        def chat(self, messages, tools=None):
            user_input = messages[-1]["content"]
            if "计算" in user_input:
                return LLMResponse(
                    content="", tool_calls=[{"name": "calculator", "args": {}, "id": "t1"}],
                    usage={"total_tokens": 60},
                )
            return LLMResponse(content="你好，我是AI助手", usage={"total_tokens": 35})

        def stream(self, messages, tools=None):
            yield LLMResponse(content="x")

    runner = EvalRunner(EvalProvider())
    cases = [
        EvalCase(name="计算任务", user_input="帮我计算 1+1", expected_tool="calculator"),
        EvalCase(name="普通对话", user_input="你好", expected_tool="", expected_keywords=["AI助手"]),
        EvalCase(name="工具选错", user_input="帮我计算 2+2", expected_tool="get_current_time"),
    ]
    summary = runner.run_all(cases)
    print(f"  用例数: {summary.total}")
    print(f"  通过: {summary.passed} ({summary.success_rate:.1%})")
    print(f"  工具选择正确率: {summary.tool_accuracy:.1%}")
    print(f"  平均延迟: {summary.avg_latency_s}s")
    print(f"  总Token: {summary.total_tokens}")


if __name__ == "__main__":
    print("\n" + "#" * 60)
    print("# Personal AI Agent · 阶段9 端到端演示")
    print("#" * 60)
    demo_chain()
    demo_team()
    demo_subagent()
    demo_metrics()
    demo_evaluation()
    print("\n" + "#" * 60)
    print("# 演示完成！所有模块工作正常。")
    print("#" * 60)
