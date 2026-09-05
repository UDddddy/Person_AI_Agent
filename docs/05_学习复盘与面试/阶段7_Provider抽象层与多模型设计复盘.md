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

## 九、专题：真流式落地——从"一次性吐全文"到逐 token

阶段7虽然在接口里定义了 `stream()`，但流式端点 `/api/chat_graph_stream` 一直是"假流式"：前端要等模型全部生成完，才一次性看到整段文字。本专题记录这次排查的完整过程，**核心价值是两个叠加的根因和对应的技术选型**。

### 9.1 现象

前端切到"流式"模式发消息，没有打字机效果：等待几秒后整段回答一次性出现。但用 MockProvider 跑单测又是"逐字"的，造成"单测过了、实际不行"的迷惑现象。

### 9.2 第一层根因：图节点用的是同步 chat()

改造前 `stream_graph.py` 的 agent_node：

```python
def agent_node(state):
    resp = provider.chat(...)          # ← 同步调用，一次性拿完整响应
    return {"messages": [AIMessage(content=resp.content, ...)]}
```

外层用 LangGraph 的 `stream_mode="messages"` 想拿逐 token 增量，但节点内部是**同步阻塞**调用 `provider.chat()`——它必须等模型把整段话生成完才返回，LangGraph 只能在节点跑完后拿到一条完整 AIMessage，自然没有增量。`stream_mode="messages"` 捕获的是"langchain ChatModel 内部的 callback 增量"，而我们的 provider 是框架无关的普通同步类，根本没有 callback 可捕获。

### 9.3 技术选择（一）：StreamWriter + stream_mode="custom"

有三个候选方案：

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. StreamWriter + custom mode | 节点内调 `provider.stream()`，用 LangGraph 注入的 `StreamWriter` 逐块写入，外层 `stream_mode="custom"` 接收 | 保持 LangGraph 图架构（agent↔tools 循环、条件边都不变），与非流式路径同构 | 需要理解 custom 流模式 | **选用** |
| B. 绕过 LangGraph 手写 agent loop | 流式端点自己写 while 循环：stream→累积→判 tool_calls→执行工具→再 stream | 最直白、完全可控 | 重复实现一遍图的循环逻辑，流式/非流式两套分叉 | 备选 |
| C. 改回 langchain ChatOpenAI | 用 `ChatOpenAI().stream()` 让 messages mode 能捕获增量 | 改动小 | 违背阶段7"provider 框架无关"的设计，重新绑死 langchain | 否决 |

**选 A 的原因**：阶段7刚把业务层从 langchain ChatModel 解耦出来，不能为了流式又退回去；而方案 B 会让同一条 Agent 决策链维护两份循环。方案 A 只在节点内部把 `chat()` 换成 `stream()`，图的拓扑（节点、条件边、工具回环）原封不动。

落地代码：

```python
from langgraph.types import StreamWriter

def agent_node(state, writer: StreamWriter):      # LangGraph 自动注入 writer
    full_content = ""
    tc_buf = {}                                  # 流式 tool_calls 按 index 累积
    for resp in provider.stream(to_openai_messages(state["messages"]), tools=TOOL_SCHEMA):
        if resp.content:                         # 文本增量：边累积边推给外层
            full_content += resp.content
            writer({"type": "token", "content": resp.content})
        for tc in resp.tool_calls:               # 工具参数增量（见 9.6）
            ...
    return {"messages": [AIMessage(content=full_content, tool_calls=final_tool_calls)]}
```

外层生成器从 messages mode 改成 custom mode：

```python
for event in compiled.stream(inputs, stream_mode="custom"):
    yield (event["type"], event.get("content"))   # event 就是 writer() 写入的 dict
```

### 9.4 第二层根因：async 端点里跑同步阻塞（真正的元凶）

改完第一层，直接在 Python 里调 `stream_graph_events()` 已经能逐 token 了，但**通过 HTTP 访问 SSE 端点，所有 token 仍然挤在同一毫秒到达**。根因在 FastAPI 端点：

```python
@app.post("/api/chat_graph_stream")
async def chat_graph_stream_endpoint(request):     # async 端点 → 跑在事件循环线程
    async def event_generator():
        for event in stream_graph_events(...):     # ← 同步生成器，内部是同步 openai HTTP 请求
            yield f"data: ..."                     # 同步 I/O 把事件循环卡死了
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**原理（关键）**：`async def` 的代码跑在单线程事件循环上。`for` 迭代同步生成器时，里面的 openai 同步 HTTP 请求会**阻塞整个事件循环线程**；这期间 `yield` 出来的 SSE 字节虽然进了发送队列，但事件循环被占着、没机会执行真正的 socket 发送。直到模型全部生成完、同步阻塞解除，事件循环才一口气把积压的所有数据 flush 出去——表现就是"等几秒，全文一次性出现"。

> 这也解释了为什么单测没问题：单测直接迭代同步生成器，同一个线程边取边处理，不存在"另一个发送协程被阻塞"的问题。**问题只在 async 运行时里暴露。**

### 9.5 技术选择（二）：iterate_in_threadpool

三个候选：

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. iterate_in_threadpool | 保持 async 端点，用 Starlette 的 `iterate_in_threadpool` 把同步生成器的每次 `next()` 丢到线程池 | 改动最小，只包一层；事件循环在等待线程时能继续发送 | 每次迭代有轻微线程切换开销（对 LLM 这种秒级延迟可忽略） | **选用** |
| B. 端点改成同步 def | `def`（非 async）端点，Starlette 自动把整个响应迭代放线程池 | 不用引入新 API | 同步生成器要写成普通函数嵌套，和项目其他 async 端点风格不统一 | 备选 |
| C. 全异步化 | 换 AsyncOpenAI + LangGraph `astream` + async provider | 最彻底、高并发性能最好 | 改动面大：provider 接口、两个实现、业务层全要加 async 版本 | 后续优化 |

**选 A 的原因**：用最小改动解决阻塞，且不破坏现有同步 provider 架构。LLM 调用是秒级 I/O 密集型，线程切换的微秒级开销完全可以忽略；方案 C 的全异步是高并发场景才需要的优化，当前单机学习项目用不上（避免过度设计）。

落地：

```python
from starlette.concurrency import iterate_in_threadpool

async def event_generator():
    sync_iter = stream_graph_events(...)                 # 同步生成器
    async for event in iterate_in_threadpool(sync_iter): # 每次 next 在线程池执行
        yield f"data: {json.dumps(...)}\n\n"             # 事件循环不被阻塞，逐条实时发出
```

`iterate_in_threadpool` 的本质：把同步迭代器的每一步 `__next__` 用 `anyio.to_thread.run_sync` 丢到工作线程，事件循环 `await` 它的期间可以去执行 SSE 字节的发送——于是 token 之间真正"流"了起来。

### 9.6 附带难点：流式 tool_calls 的增量累积

非流式 `chat()` 一次返回完整的 `tool_calls=[{name, args(dict), id}]`；但流式时模型是**逐字吐出 arguments JSON 字符串**的，每个 chunk 只带一小段，且靠 `index` 区分并行的多个工具调用：

```python
# openai_compat.stream() 每个 chunk yield 的是片段：
{"name": "calculator", "args": "{\"expr", "id": "call_1", "index": 0}
{"args": "ession", "index": 0}        # name/id 只在首块出现
{"args": ": 1+1}", "index": 0}
```

agent_node 必须按 `index` 用 dict 累积 `args` 字符串，流结束后再 `json.loads` 成 dict；工具名首次出现时推一次 `tool_call` 事件（用 `emitted` 标志位防重复）。**这是流式 function calling 和非流式最大的实现差异。**

### 9.7 排查方法论：分层定位，逐层排除

这次没有一上来就猜，而是从底向上逐层用时间戳验证，每一层都能独立证伪：

```
① provider.stream()        直接调，打印每个 chunk 时间 → 51 chunk 逐个到 ✅（排除模型层）
② stream_graph_events()    直接迭代，打印 token 时间   → 跨度 3.1s 逐个到 ✅（排除图层）
③ iterate_in_threadpool    async for 包装后测时间      → 跨度 0.1s 逐个到 ✅（排除包装层）
④ HTTP SSE 端点            requests stream 测到达时间 → 跨度 0.00s 全挤一起 ❌（锁定 HTTP 层）
```

定位到第④层后，原因就是 async 里同步阻塞。**教训：改完代码一定要确认服务真的重启了**——第一次修复后测试仍失败，是因为旧 uvicorn 进程占用端口、新进程启动失败，请求一直打到旧代码，白白绕了一圈。

### 9.8 验证数据对比

同一个请求（"写一首秋天的五言绝句并解释"，328 个 token）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 首个 token | 10.71s | 3.30s |
| 第 100 个 token | 10.71s | 3.97s |
| 最后 token | 10.71s | 6.15s |
| **到达时间跨度** | **0.00s（全积压）** | **2.86s（真流式）** |

---

## 十、面试要点速查

被问到"你这个项目怎么支持多模型"时，按这个顺序讲：

1. **痛点**：LLM 调用散落在三处，全写死 DeepSeek，换模型要改业务代码，测试必须联网
2. **方案**：LLMProvider 抽象基类 + LLMResponse 统一返回 + ProviderFactory 配置驱动
3. **接口设计**：chat/stream 两个方法，messages 用 OpenAI 格式 dict（框架无关），tool_calls 统一成 {name,args,id}
4. **实现**：OpenAICompatibleProvider（迁移现有逻辑+加异常兜底）+ MockProvider（一等公民，非测试专用）
5. **工厂**：get_provider() 从 .env 读 llm_provider，支持 kwargs 覆盖，未知 provider 明确报错
6. **改造**：graph_agent 和 stream_graph 都改用 provider，删了 ChatOpenAI 和直接 client 调用，call_llm 从20行缩到4行
7. **测试亮点**：mock openai client 测解析逻辑、测 tools=None 不传、端到端用 MockProvider 跑通完整 Agent
8. **设计权衡**：provider 层不依赖 langchain（框架无关）、模块级单例（连接池复用+测试好 patch）、usage 做空值保护
9. **真流式（加分项）**：节点用 provider.stream() + LangGraph StreamWriter（custom mode）逐块推；SSE 端点用 iterate_in_threadpool 把同步生成器放线程池，避免 async 里同步 I/O 阻塞事件循环导致"假流式"

### 流式专项面试题（自测）

**Q1：SSE / WebSocket / 普通 HTTP 响应有什么区别？LLM 流式输出为什么选 SSE？**
- 普通 HTTP：一次请求一次完整响应，要等全部生成完。
- SSE（Server-Sent Events）：**单向**（服务器→客户端）、基于 HTTP、长连接、`text/event-stream`、自动重连。LLM 流式是"客户端发一次问题、服务端持续吐 token"，正好是单向推送，SSE 最简单。
- WebSocket：**全双工双向**，适合双向高频交互（协同编辑、聊天室、语音对话），但 LLM 问答用不上客户端持续上行，属于杀鸡用牛刀，还要自己处理心跳、断线。

**Q2：为什么在 async def 里直接 for 一个同步生成器会导致"假流式"？**
- async 代码跑在单线程事件循环上；同步生成器内部的阻塞 I/O（如同步 openai 请求）会占死事件循环，期间 `yield` 的数据无法被真正发送（发送协程得不到调度），全部积压到阻塞结束后一次性 flush。解决：`iterate_in_threadpool` / `run_in_executor` 把同步迭代丢线程池，或全链路 async。

**Q3：iterate_in_threadpool 做了什么？为什么它能解决？**
- 它把同步迭代器的每次 `__next__` 通过 `anyio.to_thread.run_sync` 放到工作线程执行，事件循环 `await` 结果期间可以去调度其他协程（包括把已 yield 的 SSE 字节发给客户端），于是数据能逐条实时流出。

**Q4：LangGraph 的 stream_mode 有哪些？为什么这里用 custom 而不是 messages？**
- `values`：每步后的完整 state；`updates`：每个节点的更新；`messages`：langchain ChatModel 的 token 级 callback；`custom`：节点内通过 `StreamWriter` 自定义写入的数据。
- 本项目 provider 是框架无关的普通类、不是 langchain ChatModel，messages mode 捕获不到增量；用 StreamWriter 在节点内显式 `writer({"type":"token",...})`，custom mode 原样接收，最可控。

**Q5：流式 function calling 和非流式在处理上有什么不同？**
- 非流式一次拿到完整 tool_calls（args 已是 dict）；流式时 arguments 是逐字到达的 JSON **字符串片段**，要按 `index` 累积拼接，结束后再 `json.loads`；name/id 通常只在首块出现，需要缓存。

**Q6：怎么判断"流式没生效"卡在哪一层？**
- 从底向上分层打时间戳：①直接调 provider.stream ②直接迭代业务生成器 ③async 包装后 ④真实 HTTP SSE。哪一层的时间跨度从"有间隔"变成"全挤同一时刻"，问题就在那一层。

---

*文档更新：阶段7完成后初版，真流式落地后补充第九章专题与流式面试题 · 全量测试 158 passed*
