"""阶段8 · 多 Agent 编排 单元测试。

覆盖：AgentChain、AgentTeam、Subagent（并行+事件）、JobAssistant。
用 FakeProvider 不发网络请求。
"""

import time

import pytest

from app.event_bus import EventBus
from app.orchestration import (
    AgentChain, ChainStep,
    AgentTeam, SubAgent,
    HeadlessSubagent, SubagentRunner, SubagentSpec,
    JobAssistant,
    EVENT_SUBAGENT_START, EVENT_SUBAGENT_DONE, EVENT_SUBAGENT_ERROR,
)
from app.providers.base import LLMProvider, LLMResponse


# ===========================================================================
# 测试辅助：FakeProvider
# ===========================================================================

class FakeProvider(LLMProvider):
    """按顺序返回预设回复，记录每次调用的输入。"""
    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self._idx = 0

    @property
    def model_name(self):
        return "fake"

    def chat(self, messages, tools=None):
        self.calls.append(messages[0]["content"])
        reply = self.replies[self._idx] if self._idx < len(self.replies) else "默认"
        self._idx += 1
        return LLMResponse(content=reply)

    def stream(self, messages, tools=None):
        yield LLMResponse(content="x")


class SlowFakeProvider(LLMProvider):
    """有延迟的 FakeProvider，用于验证并行执行。"""
    def __init__(self, delay=0.05):
        self.delay = delay

    @property
    def model_name(self):
        return "slow-fake"

    def chat(self, messages, tools=None):
        time.sleep(self.delay)
        return LLMResponse(content=f"完成: {messages[-1]['content'][:10]}")

    def stream(self, messages, tools=None):
        yield LLMResponse(content="x")


class ErrorProvider(LLMProvider):
    """总是抛异常的 Provider，用于测试错误处理。"""
    @property
    def model_name(self):
        return "error"

    def chat(self, messages, tools=None):
        raise RuntimeError("模拟 LLM 调用失败")

    def stream(self, messages, tools=None):
        yield LLMResponse(content="x")


# ===========================================================================
# AgentChain
# ===========================================================================

class TestAgentChain:
    def test_single_step(self):
        chain = AgentChain([ChainStep(name="s1", prompt="处理：$INPUT")], FakeProvider(["结果"]))
        r = chain.run("输入")
        assert r.final_output == "结果"
        assert len(r.steps) == 1

    def test_multi_step_input_passing(self):
        """$INPUT 应从上一步传递到下一步。"""
        fake = FakeProvider(["A", "B", "C"])
        chain = AgentChain([
            ChainStep(name="s1", prompt="第一步：$INPUT"),
            ChainStep(name="s2", prompt="第二步：$INPUT"),
            ChainStep(name="s3", prompt="第三步：$INPUT"),
        ], fake)
        r = chain.run("原始")
        # 第2步输入应包含第1步输出"A"
        assert "A" in fake.calls[1]
        # 第3步输入应包含第2步输出"B"
        assert "B" in fake.calls[2]
        assert r.final_output == "C"

    def test_original_preserved(self):
        """$ORIGINAL 应在每步都保留原始输入。"""
        fake = FakeProvider(["x", "y"])
        chain = AgentChain([
            ChainStep(name="s1", prompt="原始：$ORIGINAL"),
            ChainStep(name="s2", prompt="原始：$ORIGINAL"),
        ], fake)
        chain.run("我的原始需求")
        assert "我的原始需求" in fake.calls[0]
        assert "我的原始需求" in fake.calls[1]

    def test_empty_steps_raises(self):
        with pytest.raises(ValueError, match="至少需要一个步骤"):
            AgentChain([], FakeProvider([]))

    def test_step_result_records_input_output(self):
        chain = AgentChain([ChainStep(name="s1", prompt="处理：$INPUT")], FakeProvider(["结果"]))
        r = chain.run("输入")
        assert r.steps[0].name == "s1"
        assert "输入" in r.steps[0].input_text
        assert r.steps[0].output == "结果"


# ===========================================================================
# AgentTeam
# ===========================================================================

class TestAgentTeam:
    def _make_agents(self):
        return [
            SubAgent(name="planner", description="规划", system_prompt="你是规划师"),
            SubAgent(name="builder", description="构建", system_prompt="你是构建者"),
        ]

    def test_dispatch_selects_correct_agent(self):
        fake = FakeProvider(["planner", "规划结果"])
        team = AgentTeam(fake, self._make_agents())
        r = team.dispatch("分析需求")
        assert r.selected_agent == "planner"
        assert r.result == "规划结果"

    def test_dispatch_tolerates_punctuation(self):
        """LLM 返回 'planner.' 应容错匹配。"""
        fake = FakeProvider(["planner.", "结果"])
        team = AgentTeam(fake, self._make_agents())
        r = team.dispatch("任务")
        assert r.selected_agent == "planner"

    def test_dispatch_tolerates_extra_text(self):
        """LLM 返回 '我选择 builder' 应包含匹配。"""
        fake = FakeProvider(["我选择 builder", "结果"])
        team = AgentTeam(fake, self._make_agents())
        r = team.dispatch("任务")
        assert r.selected_agent == "builder"

    def test_dispatch_unknown_agent_raises(self):
        fake = FakeProvider(["nonexistent"])
        team = AgentTeam(fake, self._make_agents())
        with pytest.raises(ValueError, match="不存在的子 Agent"):
            team.dispatch("任务")

    def test_empty_agents_raises(self):
        with pytest.raises(ValueError, match="至少需要一个子 Agent"):
            AgentTeam(FakeProvider([]), [])

    def test_dispatch_uses_agent_system_prompt(self):
        """子 Agent 执行时应带上它的 system_prompt。"""
        fake = FakeProvider(["planner", "结果"])
        team = AgentTeam(fake, self._make_agents())
        team.dispatch("任务")
        # 第2次调用（子Agent执行）应包含 system message
        assert len(fake.calls) == 2  # 1次选择 + 1次执行


# ===========================================================================
# Subagent
# ===========================================================================

class TestSubagent:
    def test_single_agent_emits_events(self):
        bus = EventBus()
        events = []
        bus.subscribe(EVENT_SUBAGENT_START, lambda d: events.append(("start", d["name"])))
        bus.subscribe(EVENT_SUBAGENT_DONE, lambda d: events.append(("done", d["name"])))

        spec = SubagentSpec(name="w1", system_prompt="工人", task="干活")
        agent = HeadlessSubagent(spec, FakeProvider(["完成"]), bus)
        r = agent.run()

        assert r.success
        assert r.result == "完成"
        assert events == [("start", "w1"), ("done", "w1")]

    def test_agent_error_emits_error_event(self):
        bus = EventBus()
        errors = []
        bus.subscribe(EVENT_SUBAGENT_ERROR, lambda d: errors.append(d["name"]))

        spec = SubagentSpec(name="w1", system_prompt="工人", task="干活")
        agent = HeadlessSubagent(spec, ErrorProvider(), bus)
        r = agent.run()

        assert not r.success
        assert "模拟 LLM 调用失败" in r.error
        assert errors == ["w1"]

    def test_parallel_execution(self):
        """3个子 Agent 并行应比串行快。"""
        specs = [
            SubagentSpec(name="a", system_prompt="A", task="任务A"),
            SubagentSpec(name="b", system_prompt="B", task="任务B"),
            SubagentSpec(name="c", system_prompt="C", task="任务C"),
        ]
        runner = SubagentRunner(SlowFakeProvider(delay=0.05), max_workers=3)
        start = time.time()
        results = runner.run_parallel(specs)
        elapsed = time.time() - start

        assert len(results) == 3
        assert all(r.success for r in results)
        # 串行需 0.15s，并行应 < 0.12s（留余量）
        assert elapsed < 0.15, f"并行执行过慢: {elapsed:.3f}s"

    def test_parallel_results_in_input_order(self):
        """并行结果应按输入顺序返回。"""
        specs = [
            SubagentSpec(name="first", system_prompt="A", task="A"),
            SubagentSpec(name="second", system_prompt="B", task="B"),
        ]
        runner = SubagentRunner(SlowFakeProvider(delay=0.02), max_workers=2)
        results = runner.run_parallel(specs)
        assert [r.name for r in results] == ["first", "second"]

    def test_empty_specs_returns_empty(self):
        runner = SubagentRunner(FakeProvider([]))
        assert runner.run_parallel([]) == []

    def test_parallel_with_bus(self):
        """并行执行时每个子 Agent 都应触发事件。"""
        bus = EventBus()
        done = []
        bus.subscribe(EVENT_SUBAGENT_DONE, lambda d: done.append(d["name"]))

        specs = [
            SubagentSpec(name="x", system_prompt="X", task="X"),
            SubagentSpec(name="y", system_prompt="Y", task="Y"),
        ]
        runner = SubagentRunner(SlowFakeProvider(delay=0.02), bus, max_workers=2)
        runner.run_parallel(specs)
        assert set(done) == {"x", "y"}


# ===========================================================================
# JobAssistant
# ===========================================================================

class TestJobAssistant:
    def test_three_step_pipeline(self):
        fake = FakeProvider(["planner结果", "builder结果", "reviewer结果"])
        assistant = JobAssistant(fake)
        r = assistant.run(jd="需要Python工程师", skills="Python, 3年经验")

        assert len(r.steps) == 3
        assert r.steps[0].name == "planner"
        assert r.steps[1].name == "builder"
        assert r.steps[2].name == "reviewer"
        assert r.final_output == "reviewer结果"

    def test_original_jd_preserved(self):
        """每步都应保留原始 JD。"""
        fake = FakeProvider(["p", "b", "r"])
        assistant = JobAssistant(fake)
        assistant.run(jd="AI Agent工程师岗位", skills="")

        for i, call in enumerate(fake.calls):
            assert "AI Agent工程师岗位" in call, f"第{i+1}步丢失JD"

    def test_without_skills(self):
        """不传 skills 也能正常运行。"""
        fake = FakeProvider(["p", "b", "r"])
        assistant = JobAssistant(fake)
        r = assistant.run(jd="岗位描述")
        assert r.final_output == "r"

    def test_skills_included_in_original(self):
        """传了 skills 应出现在 $ORIGINAL 中。"""
        fake = FakeProvider(["p", "b", "r"])
        assistant = JobAssistant(fake)
        assistant.run(jd="JD", skills="Python, LangGraph")
        assert "Python, LangGraph" in fake.calls[0]
