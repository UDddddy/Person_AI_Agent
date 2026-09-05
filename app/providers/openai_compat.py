"""阶段7 · 课2：OpenAI 兼容 Provider。

适用于所有 OpenAI 兼容接口：DeepSeek、OpenAI 官方、本地 vLLM、Ollama(openai兼容模式) 等。
把 graph_agent.call_llm 里写死的调用逻辑迁移到这里，业务层不再直接碰 openai client。

messages 参数是 OpenAI 格式的 dict 列表（业务层用 to_openai_messages 转换后传入）。
"""

import json
from typing import Iterator, Optional

from openai import OpenAI

from app.providers.base import LLMProvider, LLMResponse


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI 兼容接口的统一实现。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @property
    def model_name(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> LLMResponse:
        """非流式：完整调用，解析成统一 LLMResponse。"""
        kwargs = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools

        response = self._client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        # 工具调用统一成 {name, args, id} 格式
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                tool_calls.append({
                    "name": tc.function.name,
                    "args": args,
                    "id": tc.id,
                })

        # token 用量（部分 provider 可能不返回，留空）
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage=usage,
            model=self._model,
            raw=response,
        )

    def stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> Iterator[LLMResponse]:
        """流式：yield 增量 LLMResponse，content 是文本片段。

        流式 tool_calls 是增量的（arguments 逐字累积），
        业务层需要按 index 拼接，这里原样透传 delta。
        """
        kwargs = {"model": self._model, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools

        stream = self._client.chat.completions.create(**kwargs)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = delta.content or ""

            tool_calls = []
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    func = tc.function
                    tool_calls.append({
                        "name": func.name if func else "",
                        "args": func.arguments if func else "",
                        "id": tc.id,
                        "index": tc.index,
                    })

            yield LLMResponse(
                content=content,
                tool_calls=tool_calls,
                model=self._model,
            )
