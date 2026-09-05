"""阶段7 · LLM Provider 抽象层 单元测试。

覆盖：LLMResponse 统一结构、MockProvider、ProviderFactory、
OpenAICompatibleProvider（mock openai client）、配置驱动。
"""

from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import LLMResponse, LLMProvider
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.factory import get_provider


# ===========================================================================
# LLMResponse 统一结构
# ===========================================================================

class TestLLMResponse:
    def test_default_values(self):
        r = LLMResponse()
        assert r.content == ""
        assert r.tool_calls == []
        assert r.usage == {}
        assert r.model == ""
        assert r.raw is None

    def test_custom_values(self):
        r = LLMResponse(
            content="你好",
            tool_calls=[{"name": "calc", "args": {"x": 1}, "id": "t1"}],
            usage={"total_tokens": 100},
            model="deepseek-chat",
        )
        assert r.content == "你好"
        assert r.tool_calls[0]["name"] == "calc"
        assert r.usage["total_tokens"] == 100


# ===========================================================================
# MockProvider
# ===========================================================================

class TestMockProvider:
    def test_chat_returns_fixed_reply(self):
        p = MockProvider(fixed_reply="测试回复")
        r = p.chat([{"role": "user", "content": "hi"}])
        assert r.content == "测试回复"
        assert r.tool_calls == []
        assert r.model == "mock-model"

    def test_chat_with_tools_ignored(self):
        """Mock 不处理 tools，仍返回固定回复。"""
        p = MockProvider()
        r = p.chat([], tools=[{"type": "function"}])
        assert r.content == "这是 Mock 回复"

    def test_stream_yields_chars(self):
        p = MockProvider(fixed_reply="abc")
        chunks = list(p.stream([]))
        assert len(chunks) == 3
        assert "".join(c.content for c in chunks) == "abc"

    def test_model_name(self):
        p = MockProvider(model="my-mock")
        assert p.model_name == "my-mock"

    def test_is_llm_provider_subclass(self):
        assert isinstance(MockProvider(), LLMProvider)


# ===========================================================================
# ProviderFactory
# ===========================================================================

class TestProviderFactory:
    def test_create_mock(self):
        p = get_provider("mock")
        assert isinstance(p, MockProvider)

    def test_create_mock_with_custom_reply(self):
        p = get_provider("mock", fixed_reply="自定义")
        assert p.chat([]).content == "自定义"

    def test_create_openai_compat(self):
        p = get_provider(
            "openai_compat",
            api_key="sk-test",
            base_url="https://test/v1",
            model="test-model",
        )
        assert isinstance(p, OpenAICompatibleProvider)
        assert p.model_name == "test-model"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="未知 provider"):
            get_provider("anthropic")


# ===========================================================================
# OpenAICompatibleProvider（mock openai client）
# ===========================================================================

def _make_mock_openai_response(content="你好", tool_calls=None, usage=None):
    """构造一个模拟的 OpenAI chat completion 响应。"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = msg

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class TestOpenAICompatibleProvider:
    def _make_provider(self):
        p = OpenAICompatibleProvider(
            api_key="sk-test", base_url="https://test/v1", model="test-model"
        )
        return p

    def test_chat_parses_content(self):
        p = self._make_provider()
        mock_resp = _make_mock_openai_response(content="测试内容")
        p._client.chat.completions.create = MagicMock(return_value=mock_resp)

        r = p.chat([{"role": "user", "content": "hi"}])
        assert r.content == "测试内容"
        assert r.tool_calls == []
        assert r.model == "test-model"

    def test_chat_parses_tool_calls(self):
        p = self._make_provider()
        # 模拟 OpenAI 的 tool_call 对象
        tc = MagicMock()
        tc.function.name = "calculator"
        tc.function.arguments = '{"expression": "1+1"}'
        tc.id = "call_123"
        mock_resp = _make_mock_openai_response(content="", tool_calls=[tc])
        p._client.chat.completions.create = MagicMock(return_value=mock_resp)

        r = p.chat([], tools=[{"type": "function"}])
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["name"] == "calculator"
        assert r.tool_calls[0]["args"] == {"expression": "1+1"}
        assert r.tool_calls[0]["id"] == "call_123"

    def test_chat_parses_usage(self):
        p = self._make_provider()
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 20
        usage.total_tokens = 30
        mock_resp = _make_mock_openai_response(content="hi", usage=usage)
        p._client.chat.completions.create = MagicMock(return_value=mock_resp)

        r = p.chat([])
        assert r.usage == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    def test_chat_passes_tools_when_provided(self):
        p = self._make_provider()
        mock_resp = _make_mock_openai_response()
        mock_create = MagicMock(return_value=mock_resp)
        p._client.chat.completions.create = mock_create

        tools = [{"type": "function", "function": {"name": "x"}}]
        p.chat([], tools=tools)
        _, kwargs = mock_create.call_args
        assert kwargs["tools"] == tools

    def test_chat_no_tools_when_none(self):
        p = self._make_provider()
        mock_resp = _make_mock_openai_response()
        mock_create = MagicMock(return_value=mock_resp)
        p._client.chat.completions.create = mock_create

        p.chat([])
        _, kwargs = mock_create.call_args
        assert "tools" not in kwargs

    def test_stream_yields_content_deltas(self):
        p = self._make_provider()
        # 模拟流式 chunk
        chunks = []
        for char in "ab":
            delta = MagicMock()
            delta.content = char
            delta.tool_calls = None
            choice = MagicMock()
            choice.delta = delta
            chunk = MagicMock()
            chunk.choices = [choice]
            chunks.append(chunk)
        p._client.chat.completions.create = MagicMock(return_value=iter(chunks))

        results = list(p.stream([]))
        assert "".join(r.content for r in results) == "ab"

    def test_stream_skips_empty_choices(self):
        p = self._make_provider()
        empty_chunk = MagicMock()
        empty_chunk.choices = []
        normal_chunk = MagicMock()
        normal_chunk.choices = [MagicMock(delta=MagicMock(content="x", tool_calls=None))]
        p._client.chat.completions.create = MagicMock(
            return_value=iter([empty_chunk, normal_chunk])
        )

        results = list(p.stream([]))
        assert len(results) == 1
        assert results[0].content == "x"


# ===========================================================================
# 配置驱动
# ===========================================================================

class TestConfig:
    def test_llm_provider_default(self, monkeypatch):
        from app.config import Settings
        # 清除环境变量，避免用户 .env 里的 LLM_PROVIDER 覆盖默认值
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        s = Settings(
            llm_api_key="test", llm_base_url="https://test", llm_model="test",
            _env_file=None,
        )
        assert s.llm_provider == "openai_compat"

    def test_llm_provider_can_override(self, monkeypatch):
        from app.config import Settings
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        s = Settings(
            llm_api_key="test", llm_base_url="https://test",
            llm_model="test", llm_provider="mock",
            _env_file=None,
        )
        assert s.llm_provider == "mock"
