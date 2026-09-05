"""阶段8 · 课2：Agent Team（调度器模式）。

主 Agent 持有多个专项子 Agent，根据任务动态选择最合适的一个执行。
和 Chain 的区别：
- Chain：固定顺序，每步都执行
- Team：动态调度，只选一个最合适的子 Agent 执行（项目经理派活）

子 Agent 是无状态的纯文本处理器：system_prompt + task → provider.chat → 结果。
"""

from dataclasses import dataclass
from typing import Optional

from app.providers.base import LLMProvider


@dataclass
class SubAgent:
    """专项子 Agent 的定义。"""
    name: str
    description: str       # 用于主 Agent 选择时的描述
    system_prompt: str     # 子 Agent 的人设/职责


@dataclass
class DispatchResult:
    """调度结果。"""
    selected_agent: str       # 被选中的子 Agent 名
    task: str                 # 原始任务
    result: str               # 子 Agent 的执行结果


class AgentTeam:
    """主 Agent + 多个子 Agent 的团队。"""

    def __init__(self, provider: LLMProvider, agents: list[SubAgent]):
        if not agents:
            raise ValueError("AgentTeam 至少需要一个子 Agent")
        self.provider = provider
        self.agents = {a.name: a for a in agents}

    def _build_selection_prompt(self, task: str) -> str:
        """构造选择子 Agent 的 prompt。

        列出所有子 Agent 的 name+description，让 LLM 只返回名字。
        """
        agent_list = "\n".join(
            f"- {a.name}: {a.description}"
            for a in self.agents.values()
        )
        return (
            f"你是一个调度器。根据任务选择最合适的子 Agent。\n"
            f"只返回子 Agent 的名字，不要解释。\n\n"
            f"可用子 Agent：\n{agent_list}\n\n"
            f"任务：{task}\n\n"
            f"选择："
        )

    def dispatch(self, task: str) -> DispatchResult:
        """调度：主 Agent 选子 Agent → 子 Agent 执行 → 返回结果。

        Args:
            task: 任务描述

        Returns:
            DispatchResult：选中的子 Agent + 执行结果

        Raises:
            ValueError: LLM 选了不存在的子 Agent
        """
        # 第1步：主 Agent 选择子 Agent
        selection_prompt = self._build_selection_prompt(task)
        selection_resp = self.provider.chat([{"role": "user", "content": selection_prompt}])
        selected_name = selection_resp.content.strip()

        # 容错：LLM 可能返回带标点或多余文字，尝试匹配
        selected_name = self._match_agent_name(selected_name)
        if selected_name not in self.agents:
            raise ValueError(f"调度器选择了不存在的子 Agent: {selected_name!r}")

        # 第2步：子 Agent 执行
        agent = self.agents[selected_name]
        exec_resp = self.provider.chat([
            {"role": "system", "content": agent.system_prompt},
            {"role": "user", "content": task},
        ])

        return DispatchResult(
            selected_agent=selected_name,
            task=task,
            result=exec_resp.content or "",
        )

    def _match_agent_name(self, raw: str) -> str:
        """容错匹配 LLM 返回的子 Agent 名。

        LLM 可能返回 "planner"、"planner."、"我选择 planner" 等，
        尝试从返回文本中提取已知的子 Agent 名。
        """
        raw = raw.strip().rstrip("。.!！")
        # 精确匹配
        if raw in self.agents:
            return raw
        # 包含匹配：返回文本里包含某个子 Agent 名
        for name in self.agents:
            if name in raw:
                return name
        return raw  # 没匹配到，原样返回（上层会报错）
