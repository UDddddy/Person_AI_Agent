"""阶段7 · 课3：Mock Provider。

不发真实网络请求，返回预设回复。用于：
- 单元测试（不依赖 API key 和网络）
- 无网络环境下的演示
- 调试 Agent 逻辑（排除 LLM 本身的不确定性）
"""

from typing import Iterator, Optional

from app.providers.base import LLMProvider, LLMResponse


class MockProvider(LLMProvider):
    """返回固定回复的 Mock Provider。"""

    def __init__(self, fixed_reply: str = "这是 Mock 回复", model: str = "mock-model"):
        self._reply = fixed_reply
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> LLMResponse:
        """直接返回预设回复。"""
        return LLMResponse(
            content=self._reply,
            tool_calls=[],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=self._model,
        )

    def stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> Iterator[LLMResponse]:
        """按字符 yield，模拟流式输出。"""
        for char in self._reply:
            yield LLMResponse(content=char, model=self._model)
