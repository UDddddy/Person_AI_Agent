"""阶段7 · 课3：Provider 工厂。

根据配置（.env 的 llm_provider 字段）创建对应的 LLMProvider 实例。
业务层只调 get_provider()，不关心具体实现类。

支持的 provider：
- openai_compat：OpenAI 兼容接口（DeepSeek / OpenAI / 本地 vLLM 等）
- mock：返回固定回复，用于测试和无网络演示
"""

from app.config import setting
from app.providers.base import LLMProvider
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatibleProvider

# 常见模型服务商名 → 内部 provider 类的别名映射
# 这些服务商都提供 OpenAI 兼容接口，统一走 OpenAICompatibleProvider
_ALIASES = {
    "deepseek": "openai_compat",
    "openai": "openai_compat",
    "qwen": "openai_compat",
    "tongyi": "openai_compat",
    "glm": "openai_compat",
    "zhipu": "openai_compat",
    "moonshot": "openai_compat",
    "kimi": "openai_compat",
    "vllm": "openai_compat",
    "ollama": "openai_compat",
    "local": "openai_compat",
}


def get_provider(provider_name: str = None, **kwargs) -> LLMProvider:
    """根据名称创建 Provider 实例。

    Args:
        provider_name: 显式指定 provider 名；为 None 时从 setting.llm_provider 读取
        **kwargs: 覆盖默认配置（api_key / base_url / model / fixed_reply 等）

    Returns:
        LLMProvider 实例

    Raises:
        ValueError: 未知 provider 名称
    """
    name = (provider_name or setting.llm_provider).lower()
    name = _ALIASES.get(name, name)  # 别名解析：deepseek → openai_compat

    if name == "openai_compat":
        return OpenAICompatibleProvider(
            api_key=kwargs.get("api_key") or setting.llm_api_key,
            base_url=kwargs.get("base_url") or setting.llm_base_url,
            model=kwargs.get("model") or setting.llm_model,
        )

    if name == "mock":
        return MockProvider(
            fixed_reply=kwargs.get("fixed_reply", "这是 Mock 回复"),
            model=kwargs.get("model") or "mock-model",
        )

    raise ValueError(
        f"未知 provider: {name!r}，支持: openai_compat, mock"
    )
