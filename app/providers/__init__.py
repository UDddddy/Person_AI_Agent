"""阶段7：LLM Provider 抽象层。

统一所有模型供应商的调用接口，业务层只依赖 LLMProvider，
通过 get_provider() 根据配置创建具体实现。
"""

from app.providers.base import LLMProvider, LLMResponse
from app.providers.factory import get_provider
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatibleProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "get_provider",
    "MockProvider",
    "OpenAICompatibleProvider",
]
