"""阶段9 · 课2：评估框架 EvalRunner。

轻量评估：给定用例（输入 + 期望工具 + 期望关键词），调 LLM 检查：
1. 工具选择正确率：LLM 返回的 tool_calls 是否是期望的工具
2. 任务完成率：输出是否包含期望关键词
3. 延迟：每次 LLM 调用耗时
4. Token 成本：prompt/completion/total tokens

设计为组件级评估（直接调 provider.chat），不依赖完整图，更轻量更精确。
"""

import time
from dataclasses import dataclass, field

from app.providers.base import LLMProvider
from tools.registry import TOOL_SCHEMA


@dataclass
class EvalCase:
    """单个评估用例。"""
    name: str
    user_input: str
    expected_tool: str = ""          # 期望调用的工具名；空字符串表示期望不调工具
    expected_keywords: list[str] = field(default_factory=list)  # 期望输出包含的关键词


@dataclass
class EvalResult:
    """单个用例的评估结果。"""
    name: str
    passed: bool
    expected_tool: str
    actual_tool: str
    tool_correct: bool
    keywords_matched: list[str]
    keywords_missing: list[str]
    output: str
    latency_s: float
    token_usage: dict


@dataclass
class EvalSummary:
    """评估汇总报告。"""
    total: int
    passed: int
    success_rate: float
    tool_accuracy: float
    avg_latency_s: float
    total_tokens: int
    results: list[EvalResult]


class EvalRunner:
    """评估运行器：批量跑用例并汇总指标。"""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def run_case(self, case: EvalCase) -> EvalResult:
        """跑单个用例。"""
        messages = [
            {"role": "system", "content": "You are a helpful assistant with tools."},
            {"role": "user", "content": case.user_input},
        ]

        t0 = time.perf_counter()
        resp = self.provider.chat(messages, tools=TOOL_SCHEMA)
        latency = time.perf_counter() - t0

        # 工具选择检查
        actual_tool = ""
        if resp.tool_calls:
            actual_tool = resp.tool_calls[0]["name"]
        tool_correct = (actual_tool == case.expected_tool)

        # 关键词检查
        output = resp.content or ""
        matched = [kw for kw in case.expected_keywords if kw in output]
        missing = [kw for kw in case.expected_keywords if kw not in output]

        # 通过条件：工具正确 且 所有关键词都命中
        passed = tool_correct and len(missing) == 0

        return EvalResult(
            name=case.name,
            passed=passed,
            expected_tool=case.expected_tool,
            actual_tool=actual_tool,
            tool_correct=tool_correct,
            keywords_matched=matched,
            keywords_missing=missing,
            output=output,
            latency_s=latency,
            token_usage=resp.usage,
        )

    def run_all(self, cases: list[EvalCase]) -> EvalSummary:
        """批量跑用例，返回汇总报告。"""
        results = [self.run_case(case) for case in cases]

        passed = sum(1 for r in results if r.passed)
        tool_correct = sum(1 for r in results if r.tool_correct)
        avg_latency = sum(r.latency_s for r in results) / len(results) if results else 0
        total_tokens = sum(
            (r.token_usage or {}).get("total_tokens", 0) for r in results
        )

        return EvalSummary(
            total=len(results),
            passed=passed,
            success_rate=passed / len(results) if results else 0,
            tool_accuracy=tool_correct / len(results) if results else 0,
            avg_latency_s=round(avg_latency, 4),
            total_tokens=total_tokens,
            results=results,
        )

    def print_report(self, summary: EvalSummary):
        """打印人类可读的评估报告。"""
        print(f"\n{'='*60}")
        print(f"评估报告：{summary.total} 个用例")
        print(f"{'='*60}")
        print(f"  成功率：{summary.passed}/{summary.total} ({summary.success_rate:.1%})")
        print(f"  工具选择正确率：{summary.tool_accuracy:.1%}")
        print(f"  平均延迟：{summary.avg_latency_s:.3f}s")
        print(f"  总 Token：{summary.total_tokens}")
        print(f"{'-'*60}")
        for r in summary.results:
            status = "PASS" if r.passed else "FAIL"
            tool_info = f"工具: 期望={r.expected_tool or '无'} 实际={r.actual_tool or '无'} {'✓' if r.tool_correct else '✗'}"
            kw_info = f"关键词: 命中={r.keywords_matched} 缺失={r.keywords_missing}" if (r.keywords_matched or r.keywords_missing) else ""
            print(f"  [{status}] {r.name}")
            print(f"         {tool_info}")
            if kw_info:
                print(f"         {kw_info}")
            print(f"         延迟: {r.latency_s:.3f}s | 输出: {r.output[:50]}...")
        print(f"{'='*60}\n")
