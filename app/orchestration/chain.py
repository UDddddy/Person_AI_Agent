"""阶段8 · 课1：Agent Chain（顺序流水线）。

定义多个步骤，按顺序执行：
- 每步的输出作为下一步的 $INPUT
- $ORIGINAL 始终保留用户最初意图，防止多步后意图漂移
- 每步调 LLMProvider.chat，不涉及工具调用（纯文本转换流水线）

适用场景：有明确步骤顺序的任务（如 分析→生成→复核）。
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from app.providers.base import LLMProvider


@dataclass
class ChainStep:
    """流水线的一个步骤。

    prompt 里支持两个变量：
    - $INPUT：上一步的输出（第一步用原始输入）
    - $ORIGINAL：用户最初的输入（全程不变）
    """
    name: str
    prompt: str


@dataclass
class StepResult:
    """单步执行结果。"""
    name: str
    input_text: str
    output: str


@dataclass
class ChainResult:
    """整条流水线的执行结果。"""
    final_output: str
    steps: list = field(default_factory=list)  # list[StepResult]


def _render_prompt(prompt: str, input_text: str, original: str) -> str:
    """把 prompt 里的 $INPUT 和 $ORIGINAL 替换成实际值。

    用正则替换，避免 str.replace 在 $INPUT 包含 $ORIGINAL 时出问题。
    """
    result = prompt
    result = re.sub(r"\$INPUT\b", input_text, result)
    result = re.sub(r"\$ORIGINAL\b", original, result)
    return result


class AgentChain:
    """顺序执行多个 ChainStep 的流水线。"""

    def __init__(self, steps: list[ChainStep], provider: LLMProvider):
        if not steps:
            raise ValueError("AgentChain 至少需要一个步骤")
        self.steps = steps
        self.provider = provider

    def run(self, original_input: str) -> ChainResult:
        """执行整条流水线。

        Args:
            original_input: 用户最初的输入，作为第一步的 $INPUT 和全程的 $ORIGINAL

        Returns:
            ChainResult：含最终输出和每步的中间结果
        """
        current_input = original_input
        step_results = []

        for step in self.steps:
            rendered = _render_prompt(step.prompt, current_input, original_input)
            resp = self.provider.chat([{"role": "user", "content": rendered}])
            output = resp.content or ""

            step_results.append(StepResult(
                name=step.name,
                input_text=rendered,
                output=output,
            ))
            current_input = output  # 本步输出作为下一步的 $INPUT

        return ChainResult(
            final_output=current_input,
            steps=step_results,
        )
