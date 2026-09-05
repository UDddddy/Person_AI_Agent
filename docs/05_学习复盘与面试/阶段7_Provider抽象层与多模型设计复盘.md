# 阶段7 设计复盘：LLM Provider 抽象层与多模型切换

> 本文是阶段7开发完成后的设计决策复盘，既是学习笔记，也是面试讲项目时的核心素材。
> 覆盖：为什么要做 Provider 抽象、接口怎么设计、两个实现的差异、工厂模式配置驱动、主链路改造、测试设计。

---

## 一、阶段7要解决什么问题

阶段7之前，LLM 调用散落在三个地方，且全部写死 DeepSeek：

| 位置 | 调用方式 | 问题 |
|---|---|---|
| `app/llm.py` | 模块级 `OpenAI(client)` + `chat()` | 阶段1遗留，graph_agent 已不用 |
| `app/graph_agent.py` | `call_llm()` 直接 `client.chat.completions.create` | 写死 model/base_url，换模型要改业务代码 |
| `app/stream_graph.py` | `ChatOpenAI(...).bind_tools(...)` | 依赖 langchain 的 ChatOpenAI，和 graph_agent 两套调用逻辑 |

三个问题：
1. **换模型要改业务代码**：想从 DeepSeek 切到 OpenAI 官方或本地模型，得改 `call_llm` 里的 `client` 初始化，业务逻辑和模型选择耦合死了。
2. **两套调用逻辑不一致**：graph_agent 手写 OpenAI 调用，stream_graph 用 langchain 封装，行为可能有差异。
3. **测试必须联网**：没有 Mock，单元测试要么 patch 深层 `client.chat.completions.create`，要么真发请求。

阶段7的解法：**引入 LLMProvider 抽象层，业务层只依赖接口，具体模型由配置选择**。

---

## 二、整体架构

```
app/providers/
├── __init__.py          ← 统一导出
├── base.py              ← LLMProvider 抽象基类 + LLMResponse 统一返回
├── openai_compat.py     ← OpenAI 兼容实现（DeepSeek/OpenAI/本地vLLM）
├── mock.py              ← Mock 实现（测试/无网络用）
└── factory.py           ← get_provider() 工厂，配置驱动创建

业务层（graph_agent / stream_graph）
  ↓ 只调用
provider = get_provider()       ← 根据 .env 的 llm_provider 字段创建
resp = provider.chat(messages, tools)
```

**依赖方向**：业务层 → 抽象接口 ← 具体实现。业务层不认识 OpenAICompatibleProvider，只认识 LLMProvider。

---

## 三、接口设计：LLMProvider + LLMResponse

### 3.1 为什么先定义统一返回结构

不同厂商的响应格式不一样：
- OpenAI：`response.choices[0].message.content`，tool_calls 在 `message.tool_calls[].function`
- Anthropic：`response.content[].text`，tool_use 在 `content[].input`
- 本地模型：可能又是另一种

如果业务层直接处理这些差异，换一个模型就要写一套解析逻辑。所以先定义 `LLMResponse` 把所有差异抹平：

```python
@dataclass
class LLMResponse:
    content: str = ""                              # 文本内容
    tool_calls: list = field(default_factory=list) # 统一成 [{name, args, id}]
    usage: dict = field(default_factory=dict)      # {prompt_tokens, completion_tokens, total_tokens}
    model: str = ""                                # 实际用的模型
    raw: object = None                             # 原始响应（调试用）
```

**关键点**：
- `tool_calls` 统一成 `{name, args, id}`，不管底层是 `function.name` 还是 `name`，provider 负责转换
- `usage` 可能为空（部分 provider 不返回 token 用量），业务层要做空值保护
- `raw` 保留原始响应，调试时能看到底层数据，但业务逻辑不依赖它

### 3.2 LLMProvider 抽象接口

```python
class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], tools=None) -> LLMResponse: ...

    @abstractmethod
    def stream(self, messages: list[dict], tools=None) -> Iterator[LLMResponse]: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...
```

**设计决策**：

1. **messages 用 OpenAI 格式 dict，不用 langchain Message**：provider 层保持框架无关。langchain Message 转 dict 的工作（`to_openai_messages`）留在业务层。这样以后换框架，provider 一行不用改。

2. **chat 和 stream 都在接口里**：非流式和流式是两种使用场景，都要统一。stream yield 的每个 `LLMResponse.content` 是增量片段，业务层拼接。

3. **tools 可选**：不是所有调用都需要工具（比如纯聊天），`tools=None` 时不传。

4. **model_name 是 property 不是方法**：模型名是 provider 的属性，不需要参数。

---

## 四、两个实现

### 4.1 OpenAICompatibleProvider

适用于所有 OpenAI 兼容接口：DeepSeek、OpenAI 官方、本地 vLLM、Ollama(openai 兼容模式) 等。

**把 graph_agent.call_llm 里写死的逻辑迁移进来**，但做了几个改进：

| 改进点 | 说明 |
|---|---|
| tool_calls 的 args 解析加 try/except | 模型可能返回非法 JSON，解析失败时存 `{"_raw": 原文}` 而不是崩 |
| usage 做空值判断 | `response.usage` 可能为 None，不强行取属性 |
| stream 的 tool_calls 带 index | 流式 tool_calls 是增量的，按 index 拼接，业务层需要 |
| 跳过空 choices | 流式 chunk 可能 `choices=[]`（心跳包），直接 continue |

**chat 核心流程**：
```
构造 kwargs(model, messages, [tools])
→ client.chat.completions.create(**kwargs)
→ 解析 message.content / message.tool_calls / response.usage
→ 包装成 LLMResponse 返回
```

### 4.2 MockProvider

**为什么 Mock 是一等公民而不是测试专用？**

- 无网络环境（飞机、地铁、CI）能跑完整 Agent 逻辑
- 调试 Agent 编排时排除 LLM 本身的不确定性（模型抽风 vs 代码 bug 分不清）
- 演示时不需要 API key

实现极简：
- `chat`：直接返回预设的 `fixed_reply`
- `stream`：按字符 yield，模拟流式输出
- `usage`：全 0（不消耗真实 token）

---

## 五、ProviderFactory：配置驱动

```python
def get_provider(provider_name=None, **kwargs) -> LLMProvider:
    name = provider_name or setting.llm_provider
    if name == "openai_compat":
        return OpenAICompatibleProvider(api_key=..., base_url=..., model=...)
    if name == "mock":
        return MockProvider(fixed_reply=..., model=...)
    raise ValueError(f"未知 provider: {name}")
```

**设计要点**：

1. **默认从配置读，也可显式传**：`get_provider()` 用 `.env` 的 `llm_provider`；`get_provider("mock")` 显式指定。测试时显式传 mock，不依赖环境。

2. **kwargs 覆盖默认配置**：`get_provider("openai_compat", model="gpt-4")` 可以只覆盖 model，其余用配置。灵活但不混乱。

3. **未知 provider 明确报错**：而不是静默 fallback 到某个默认，避免配置写错了还以为在跑新模型。

4. **配置加在 Settings 里**：`llm_provider: str = "openai_compat"`，有默认值，老的 `.env` 不加这个字段也能跑（向后兼容）。

---

## 六、主链路改造

### 6.1 graph_agent.py

改造前：
```python
from app.llm import client
def call_llm(messages):
    response = client.chat.completions.create(model=..., messages=..., tools=...)
    # 手写解析 tool_calls...
```

改造后：
```python
from app.providers import get_provider
provider = get_provider()  # 模块级单例

def call_llm(messages):
    resp = provider.chat(to_openai_messages(messages), tools=TOOL_SCHEMA)
    return AIMessage(content=resp.content, tool_calls=resp.tool_calls)
```

**变化**：
- 删了 `from app.llm import client`，业务层不再直接依赖 openai
- `call_llm` 从 20 行缩到 4 行，解析逻辑全在 provider 里
- `to_openai_messages` 保留在业务层（langchain → dict 的转换是业务层职责）

### 6.2 stream_graph.py

改造前：
```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(api_key=..., base_url=..., model=...)
llm_with_tools = llm.bind_tools(TOOL_SCHEMA)

def agent_node(state):
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}
```

改造后：
```python
from app.providers import get_provider
from app.graph_agent import to_openai_messages
provider = get_provider()

def agent_node(state):
    resp = provider.chat(to_openai_messages(state["messages"]), tools=TOOL_SCHEMA)
    ai_msg = AIMessage(content=resp.content, tool_calls=resp.tool_calls)
    logger.info("agent 节点产出 (model=%s, usage=%s)", resp.model, resp.usage)
    return {"messages": [ai_msg]}
```

**变化**：
- 删了 `ChatOpenAI` 和 `bind_tools`，不再依赖 langchain 的模型封装
- 和 graph_agent 用同一套 provider + to_openai_messages，两套调用逻辑统一
- 日志新增 `model` 和 `usage`，token 用量可观测

### 6.3 为什么 provider 用模块级单例

```python
provider = get_provider()  # 模块加载时创建一次
```

- OpenAI client 内部有连接池，重复创建浪费
- 配置在运行期间不变，不需要每次重新读
- 测试时 `patch("app.graph_agent.provider", MockProvider())` 即可替换，很方便

---

## 七、测试设计

阶段7测试在 `test/test_stage7.py`，共20个，分五类。

### 7.1 分层测试

| 测试类 | 数量 | 方式 |
|---|---|---|
| TestLLMResponse | 2 | 纯 dataclass，测默认值和自定义值 |
| TestMockProvider | 5 | 不碰网络，直接调 chat/stream |
| TestProviderFactory | 4 | 测创建逻辑和未知 provider 报错 |
| TestOpenAICompatibleProvider | 8 | mock openai client，测解析逻辑 |
| TestConfig | 2 | 测 llm_provider 默认值和覆盖 |

### 7.2 关键测试手法

**手法1：mock openai client 测 OpenAICompatibleProvider**

不发真实网络请求，而是 patch provider 的 `_client`：
```python
p = OpenAICompatibleProvider(api_key="x", base_url="y", model="z")
p._client.chat.completions.create = MagicMock(return_value=mock_response)
r = p.chat([])
```
这样能精确控制返回值，测"tool_calls 解析对不对"、"usage 解析对不对"、"tools 参数传没传"。

**手法2：构造模拟的 OpenAI 响应对象**

用 `MagicMock` 模拟 `response.choices[0].message.tool_calls[].function` 这种深层嵌套，比构造真实 OpenAI 对象简单得多。

**手法3：测"tools 为 None 时不传"**

```python
p.chat([])  # 不传 tools
_, kwargs = mock_create.call_args
assert "tools" not in kwargs  # 确认没把 None 传进去
```
这是个容易忽略的边界——有些 provider 收到 `tools=None` 会报错，必须确认 None 时完全不传这个参数。

**手法4：流式测空 choices**

```python
empty_chunk.choices = []  # 模拟心跳包
normal_chunk.choices = [delta]
results = list(p.stream([]))
assert len(results) == 1  # 空 chunk 被跳过
```

### 7.3 端到端验证

用 `patch("app.graph_agent.provider", MockProvider(...))` 跑完整 `run_graph_agent`，确认：
- 返回 Mock 的预设回复
- 树形存储正常记录（system→user→assistant）
- 全程不发网络请求

---

## 八、设计哲学一句话总结

> **业务层依赖抽象，不依赖具体实现；用统一返回结构抹平厂商差异；用工厂+配置把"选哪个模型"的决策从代码里剥离出去。**

三个可迁移到任何项目的通用能力：
1. **抽象接口 + 统一返回**：把"会变化的部分"（模型厂商）隔离在接口后面
2. **Mock 一等公民**：让无网络/CI/调试都能跑完整逻辑
3. **配置驱动工厂**：切换实现不改代码，只改配置

---

## 九、面试要点速查

被问到"你这个项目怎么支持多模型"时，按这个顺序讲：

1. **痛点**：LLM 调用散落在三处，全写死 DeepSeek，换模型要改业务代码，测试必须联网
2. **方案**：LLMProvider 抽象基类 + LLMResponse 统一返回 + ProviderFactory 配置驱动
3. **接口设计**：chat/stream 两个方法，messages 用 OpenAI 格式 dict（框架无关），tool_calls 统一成 {name,args,id}
4. **实现**：OpenAICompatibleProvider（迁移现有逻辑+加异常兜底）+ MockProvider（一等公民，非测试专用）
5. **工厂**：get_provider() 从 .env 读 llm_provider，支持 kwargs 覆盖，未知 provider 明确报错
6. **改造**：graph_agent 和 stream_graph 都改用 provider，删了 ChatOpenAI 和直接 client 调用，call_llm 从20行缩到4行
7. **测试亮点**：mock openai client 测解析逻辑、测 tools=None 不传、端到端用 MockProvider 跑通完整 Agent
8. **设计权衡**：provider 层不依赖 langchain（框架无关）、模块级单例（连接池复用+测试好 patch）、usage 做空值保护

---

*文档生成时间：阶段7完成后 · 全量测试 109 passed*
