"""阶段7 · 课1：LLM Provider 抽象基类 + 统一返回结构。

设计原则：
- 业务层（graph_agent / stream_graph）只依赖 LLMProvider 接口，不关心底层是 OpenAI/DeepSeek/Mock
- messages 统一用 OpenAI 格式的 dict 列表（事实标准），业务层负责把 langchain 消息转成这个格式
- LLMResponse 是所有 provider 的统一返回，抹平各厂商响应结构差异
- provider 层不依赖 langchain，保持框架无关
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class LLMResponse:
    """统一的 LLM 返回结构。

    - content：文本内容（流式时是增量片段）
    - tool_calls：工具调用列表，统一格式 [{name, args, id}]
    - usage：token 用量 {prompt_tokens, completion_tokens, total_tokens}，可能为空
    - model：实际使用的模型名
    - raw：原始响应对象（调试用，可选）
    """
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    raw: object = None


class LLMProvider(ABC):
    """LLM Provider 抽象接口。

    所有具体 provider（OpenAI兼容、Mock、未来的 Anthropic/Google）都实现这个接口。
    业务层通过 ProviderFactory 拿到实例，只调用 chat / stream。
    """

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> LLMResponse:
        """非流式对话。

        Args:
            messages: OpenAI 格式的消息列表 [{role, content, ...}]
            tools: 工具 schema 列表（OpenAI function calling 格式）

        Returns:
            LLMResponse：完整响应
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> Iterator[LLMResponse]:
        """流式对话，yield 增量 LLMResponse。

        每个 yield 的 LLMResponse.content 是文本片段（可能为空字符串），
        tool_calls 可能在流式过程中逐步完整（具体取决于 provider 实现）。
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """当前使用的模型名（用于日志/调试）。"""
        ...
