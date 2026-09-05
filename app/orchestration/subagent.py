"""阶段8 · 课3：Subagent（无头后台子 Agent）。

无头子 Agent 的特点：
- 不直接和用户交互（headless）
- 不维护对话历史，每次执行是独立的
- 通过 EventBus 上报进度（start/done/error）
- 支持多个子 Agent 并行执行（ThreadPoolExecutor）

和 Team 的区别：
- Team：主 Agent 选一个子 Agent 同步执行，等结果
- Subagent：多个子 Agent 后台并行跑，通过事件通知，主 Agent 不阻塞等待
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from app.event_bus import EventBus
from app.providers.base import LLMProvider

# 事件类型常量
EVENT_SUBAGENT_START = "subagent.start"
EVENT_SUBAGENT_DONE = "subagent.done"
EVENT_SUBAGENT_ERROR = "subagent.error"


@dataclass
class SubagentSpec:
    """无头子 Agent 的定义。"""
    name: str
    system_prompt: str
    task: str            # 要执行的任务


@dataclass
class SubagentResult:
    """单个子 Agent 的执行结果。"""
    name: str
    success: bool
    result: str = ""
    error: str = ""


class HeadlessSubagent:
    """无头子 Agent：执行任务，通过 EventBus 上报状态。"""

    def __init__(self, spec: SubagentSpec, provider: LLMProvider, bus: Optional[EventBus] = None):
        self.spec = spec
        self.provider = provider
        self.bus = bus

    def run(self) -> SubagentResult:
        """执行任务，上报 start/done/error 事件。"""
        if self.bus:
            self.bus.emit(EVENT_SUBAGENT_START, {"name": self.spec.name, "task": self.spec.task})

        try:
            resp = self.provider.chat([
                {"role": "system", "content": self.spec.system_prompt},
                {"role": "user", "content": self.spec.task},
            ])
            result = SubagentResult(
                name=self.spec.name,
                success=True,
                result=resp.content or "",
            )
            if self.bus:
                self.bus.emit(EVENT_SUBAGENT_DONE, {"name": self.spec.name, "result": result.result})
            return result

        except Exception as e:
            result = SubagentResult(
                name=self.spec.name,
                success=False,
                error=str(e),
            )
            if self.bus:
                self.bus.emit(EVENT_SUBAGENT_ERROR, {"name": self.spec.name, "error": str(e)})
            return result


class SubagentRunner:
    """管理多个无头子 Agent，支持并行执行。"""

    def __init__(self, provider: LLMProvider, bus: Optional[EventBus] = None, max_workers: int = 4):
        self.provider = provider
        self.bus = bus
        self.max_workers = max_workers

    def run_parallel(self, specs: list[SubagentSpec]) -> list[SubagentResult]:
        """并行执行多个子 Agent。

        Args:
            specs: 子 Agent 定义列表

        Returns:
            按 specs 顺序排列的结果列表
        """
        if not specs:
            return []

        results: dict[str, SubagentResult] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有子 Agent
            future_to_name = {}
            for spec in specs:
                agent = HeadlessSubagent(spec, self.provider, self.bus)
                future = executor.submit(agent.run)
                future_to_name[future] = spec.name

            # 收集结果（完成顺序不固定，但最终按输入顺序返回）
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    results[name] = SubagentResult(name=name, success=False, error=str(e))

        # 按输入顺序返回
        return [results[spec.name] for spec in specs]
