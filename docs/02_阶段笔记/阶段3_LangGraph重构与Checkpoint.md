# 阶段 3 学习文档：LangGraph 重构与 Checkpoint

> 适用阶段：阶段 3｜V3 LangGraph 重构与 Checkpoint（约 1.5 周）
> 技术栈：LangGraph、StateGraph、Checkpoint、SSE 流式
> 配套项目代码：
>
> `app/`
>
> 、
>
> `tools/`
>
> （阶段 1/2 产物）



***

## 0. 这份文档怎么用

阶段 3 的目标，是把你在阶段 1/2 手写的 Agent 循环（`run_agent`）**迁移成 LangGraph 状态图**。这份文档不是让你抄代码，而是让你在写每一行之前，能回答三个问题：



1. **这是什么**—— 这个组件到底在干什么？

2. **为什么用它**—— 它解决了我手写时的哪个痛点？

3. **底层原理**—— 它背后是怎么工作的？

阅读顺序：先读 §1（动机，最重要）→ §2（一张图建立心智模型）→ §3（三个核心概念）→ §4（动手迁移，对照你自己的代码）→ §5/§6（Checkpoint 和流式）。每节末尾有「对照你的项目」小框，那是你自己的思考题。



***

## 1. 先回答 "为什么要用 LangGraph"：手写 Loop 的四个痛点

回忆你阶段 1/2 的 `app/agent.py` 里的 `run_agent`，它的核心就是一个 `for` 循环：



```
for i in range(max\_iterations):

&#x20;   response = chat\_with\_tools(messages)      # 1. 调 LLM

&#x20;   if not response.tool\_calls:               # 2. 没工具调用 → 出答案

&#x20;       return response.content

&#x20;   # 3. 有工具调用 → 把 assistant 的 tool\_calls 放回 messages

&#x20;   # 4. 执行工具，把结果放回 messages

&#x20;   # 5. 回到循环顶部，再调 LLM
```

这段代码能跑，但它是 \*\*"把 Agent 写死在了一个函数里"\*\*。它有四个隐藏痛点：

### 痛点 1：状态藏在局部变量里

`messages` 是 `run_agent` 里的局部变量，它只存在于这次函数调用期间。你想在 "循环中途" 看一眼状态、改一下状态、或者把状态传给别的地方 —— 做不到，状态被函数边界困住了。

### 痛点 2：无法恢复（这是最痛的）

假设 LLM 已经调用了 3 次工具、正进行到第 4 步，**进程崩了**。你靠 SQLite 能恢复 "对话历史"（阶段 2 做的），但你**无法恢复 "执行到一半" 的状态**—— 刚才那 3 次工具调用的中间结果、当前进行到哪一步，全部丢失，只能从头再来。

### 痛点 3：难调试

你只能靠 `logger.info` 打印日志来猜流程走到了哪。没法回答 "这次请求到底经过了哪些步骤、每一步的输入输出是什么"。

### 痛点 4：难扩展

阶段 5 要加 "人工审批"（高风险操作先暂停等人类确认），阶段 6 要加 "并行子任务"。这些都要**改主循环**。每加一个功能，循环就复杂一分，最后变成没人敢动的 "意大利面条"。

### LangGraph 的答案

> **把 "循环、状态、分支、恢复" 这些通用能力，从你的业务代码里抽出来，交给框架。**

你只需要声明三样东西 —— 状态长什么样（State）、有哪些步骤（Node）、步骤怎么流转（Edge）—— 剩下的循环控制、状态传递、断点恢复、流式输出，框架替你管。这就是 "框架化" 的核心价值：**业务逻辑与执行机制解耦**。

> **对照你的项目**
>
> ：现在请你自己回答 ——
>
> `run_agent`
>
>  里的 
>
> `max_iterations`
>
>  循环，本质上是哪两种节点在交替？循环的退出条件是什么？把答案写在纸上，再往下看。



***

## 2. LangGraph 是什么：一张图读懂

**一句话：LangGraph 是用 "画流程图" 的方式构建 AI 应用。**

它把你的 Agent 运行过程建模成一张**有向图**：



```
&#x20;        ┌────────────────────────────────────┐

&#x20;        │                                    │

&#x20;        ▼                                    │

&#x20;  ┌──────────┐   条件边    ┌──────────┐      │

&#x20;  │  START   │ ──────────► │  agent   │ ──┐  │

&#x20;  └──────────┘   (入口)    └────┬─────┘   │  │

&#x20;                               │          │  │

&#x20;                       ┌───────┴───────┐  │  │

&#x20;                       │ should\_continue│ │  │

&#x20;                       │  (路由函数)     │  │  │

&#x20;                       └───┬───────┬───┘  │  │

&#x20;                           │       │      │  │

&#x20;                     去 tools    结束     │  │

&#x20;                           │       │      │  │

&#x20;                           ▼       ▼      │  │

&#x20;                    ┌──────────┐ ┌─────┐  │  │

&#x20;                    │  tools   │ │ END │  │  │

&#x20;                    └────┬─────┘ └─────┘  │  │

&#x20;                         │                │  │

&#x20;                         └────────────────┘  │

&#x20;                                 普通边      │

&#x20;                                 回到 agent ─┘
```

这个图和你手写的循环是**完全等价**的：



* `agent` 节点 = 你的 `chat_with_tools(messages)`（让 LLM 决定下一步）

* `tools` 节点 = 你的 `execute_tool(...)`（执行工具）

* `should_continue` 条件边 = 你的 `if not response.tool_calls: return`（决定结束还是继续）

* `tools → agent` 普通边 = 你的循环回跳

**区别在于**：手写时这些逻辑挤在一个 `for` 循环里；画成图后，每一步都是独立、可观察、可记录的单元。

### 三大核心概念速记



| 概念            | 是什么            | 类比        | 对应你手写的                             |
| ------------- | -------------- | --------- | ---------------------------------- |
| **State（状态）** | 所有节点共享的 "公共黑板" | 全局变量      | `messages` 列表                      |
| **Node（节点）**  | 一个执行步骤 = 一个函数  | 流水线上的一道工序 | `chat_with_tools` / `execute_tool` |
| **Edge（边）**   | 步骤间的流转规则       | 工序间的传送带   | `for` 循环 + `if` 判断                 |

### 安装



```
pip install -U langgraph langchain-core langchain-openai
```

（如果你的 LLM 走 OpenAI-compatible API，用 `langchain-openai` 里的 `ChatOpenAI`。）



***

## 3. 核心概念逐个讲透

### 3.1 State（状态）—— Agent 的 "公共黑板"

#### 是什么

State 是**贯穿整个图、所有节点都能读写**的共享数据结构。它定义了 "这张图上流转的数据长什么样"。

#### 为什么需要它

Agent 的本质就是 "**维护状态、逐步推进**"。LLM 每走一步，都要基于当前完整的状态（对话历史 + 中间结果）做决策。手写时你手动维护 `messages` 列表；LangGraph 里，State 就是那个列表（以及你想加的任何字段）的 "官方载体"。

#### 怎么定义

用 `TypedDict` 或 Pydantic 模型定义。**重点在 Reducer**：



```
from typing import TypedDict, Annotated

from langgraph.graph.message import add\_messages

class AgentState(TypedDict):

&#x20;   # add\_messages 是这个字段的 Reducer（归约器）

&#x20;   messages: Annotated\[list, add\_messages]

&#x20;   iterations: int          # 没写 Reducer，默认"覆盖"

&#x20;   max\_iterations: int
```

#### 底层原理：Reducer（归约器）—— 状态的 "合并规则"

这是 LangGraph 最核心、也最容易踩坑的概念。**问题在于**：每个节点执行完都会返回一份 "状态更新"。那这份更新怎么合并进全局状态？**是覆盖？还是追加？**—— 由 Reducer 决定。



```
\# 场景：节点返回 {"messages": \[新消息]}

\# ❌ 没有 Reducer（默认行为）：整个 messages 被覆盖 → 旧消息全丢！

\# ✅ 有 add\_messages Reducer：新消息"追加"到旧列表 → 消息都保留
```



| Reducer        | 行为          | 场景                     |
| -------------- | ----------- | ---------------------- |
| （不写）           | 覆盖旧值        | 普通标量字段（如 `iterations`） |
| `operator.add` | 列表拼接        | 累积结果                   |
| `add_messages` | 消息列表追加 / 合并 | **对话历史（最常用）**          |
| 自定义函数          | 任意合并逻辑      | 去重、合并字典等               |

> **对照你的项目**
>
> ：你阶段 2 在 
>
> `run_agent`
>
>  里手动 
>
> `messages.append(...)`
>
>  来累积消息 ——
>
> `add_messages`
>
>  就是这个动作的 "框架版"。你想想，为什么 LangGraph 要把 "追加" 设计成显式声明而不是默认行为？—— 因为
>
> **不是所有状态都该追加**
>
> ，有些字段该被新值覆盖（比如 
>
> `iterations`
>
> ），让开发者显式声明才能避免误覆盖。

### 3.2 Node（节点）—— 一个普通 Python 函数

#### 是什么

节点就是**一个普通函数**：接收当前 State，返回一份 "状态更新"（一个 dict）。



```
def call\_model(state: AgentState):

&#x20;   messages = state\["messages"]

&#x20;   response = llm.invoke(messages)          # 调 LLM

&#x20;   return {"messages": \[response],          # 返回"更新"，不返回整个 state

&#x20;           "iterations": state\["iterations"] + 1}
```

#### 关键约定（容易错！）



1. **输入**：只能读 State；**输出**：只返回**要更新的字段**，不要 `return {...整个 state...}`。

2. 返回值由 Reducer 决定如何合并（见 3.1）。

3. 节点可以是同步 / 异步函数，也可以是 LangChain 的 Runnable。

4. 还有两个特殊节点：`START`（图入口，虚拟节点）和 `END`（终止节点）。

> **对照你的项目**
>
> ：把 
>
> `call_model`
>
>  和 
>
> `execute_tool`
>
>  分别改成两个节点函数，其实只是把你 
>
> `run_agent`
>
>  里的逻辑
>
> **拆成独立函数**
>
> —— 你现在就可以动手试试，把 
>
> `chat_with_tools`
>
>  的调用封装成 
>
> `call_model(state)`
>
> ，把工具执行封装成 
>
> `call_tools(state)`
>
> 。

### 3.3 Edge / Conditional Edge（边）—— 流程怎么走

#### 普通边：固定流转



```
graph.add\_edge(START, "agent")   # 入口 → agent

graph.add\_edge("tools", "agent") # 工具执行完 → 回到 agent（形成循环！）
```

#### 条件边：根据 State 动态决定



```
from typing import Literal

def should\_continue(state: AgentState) -> Literal\["tools", "\_\_end\_\_"]:

&#x20;   last = state\["messages"]\[-1]

&#x20;   if getattr(last, "tool\_calls", None):   # LLM 要调工具？

&#x20;       return "tools"                       # → 去执行工具

&#x20;   return "\_\_end\_\_"                         # → 结束

graph.add\_conditional\_edges("agent", should\_continue)
```

#### 底层原理：条件边就是你手写 `if` 的 "图化"

你手写的 `if not response.tool_calls: return` 是一个**藏在代码里的控制流**。LangGraph 把它**提升为图上的一个显式路由函数**—— 路由函数读 State、返回 "下一个节点的名字"。好处：



* 流程结构**可视化**（`graph.get_graph().draw_mermaid_png()` 能导出图）

* 每一步流转都是**数据**，可以记录、回放、审计

* 加新分支 = 加新节点 + 改路由函数，**不用改其他节点**

> **对照你的项目**
>
> ：你的 
>
> `max_iterations`
>
>  防死循环怎么在图上体现？—— 很简单，在 
>
> `should_continue`
>
>  里判断 
>
> `if state["iterations"] >= state["max_iterations"]: return "__end__"`
>
> 。这就是把 "业务规则" 编码进图结构，而不是靠循环上限硬撑。

### 3.4 底层原理补充：Pregel 图计算模型与 "超步"

LangGraph 的底层执行引擎是 **Pregel**（Google 提出的图计算模型）。这解释了它为什么能流式、能并行、能恢复：



* **超步（Superstep）**：图的执行被切成一连串 "超步"。每个超步里，一批**相互独立**的节点并行执行；执行完一起**同步**，再进入下一个超步。

* **确定性**：每个超步的输入（当前 State）和节点逻辑是确定的 → 只要记录每次 State 快照，就能**重放 / 恢复**。

* **类比**：就像数据库事务 —— 每步要么完整提交（State 更新），要么回滚到上一个检查点。

这就是 Checkpoint（§5）能工作的根基：**因为执行是 "可记录的超步序列"，所以能 "断点续传"**。



***

## 4. 动手：把你的 run\_agent 迁到 LangGraph（核心实操）

### 4.1 你现在的手写代码（回顾）



```
\# app/agent.py（阶段 2 版，简化示意）

def run\_agent(user\_message, max\_iterations=5, session\_id="default\_session"):

&#x20;   history = load\_history(session\_id)

&#x20;   messages = \[{"role": "system", "content": "You are a helpful assistant."}, \*history,

&#x20;               {"role": "user", "content": user\_message}]

&#x20;   for i in range(max\_iterations):

&#x20;       response = chat\_with\_tools(messages)

&#x20;       if not response.tool\_calls:

&#x20;           save\_message(session\_id, "user", user\_message)

&#x20;           return response.content

&#x20;       messages.append({"role": "assistant", "content": response.content,

&#x20;                        "tool\_calls": \[...for tc in response.tool\_calls]})

&#x20;       for tool\_call in response.tool\_calls:

&#x20;           result = execute\_tool(tool\_call.function.name, tool\_call.function.arguments)

&#x20;           messages.append({"role": "tool", "tool\_call\_id": tool\_call.id, "content": str(result)})

&#x20;   return "达到最大迭代次数，未能得到最终答案。"
```

### 4.2 迁移后的 LangGraph 版本



```
\# app/agent\_graph.py（阶段 3 目标代码，示意）

from typing import TypedDict, Annotated

from langgraph.graph import StateGraph, START, END, MessageGraph

from langgraph.graph.message import add\_messages

from langchain\_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage

class AgentState(TypedDict):

&#x20;   messages: Annotated\[list, add\_messages]   # 对话历史，追加合并

&#x20;   iterations: int                           # 当前轮数，覆盖式

&#x20;   max\_iterations: int                       # 上限

\# ---- 节点 1：让 LLM 决策 ----

def call\_model(state: AgentState):

&#x20;   messages = state\["messages"]

&#x20;   response = llm.invoke(messages)           # 你的 chat\_with\_tools 底层

&#x20;   return {"messages": \[response],

&#x20;           "iterations": state\["iterations"] + 1}

\# ---- 节点 2：执行工具 ----

def call\_tools(state: AgentState):

&#x20;   # 找到最后一条 AI 消息里的所有 tool\_calls

&#x20;   last\_ai = next(m for m in reversed(state\["messages"]) if isinstance(m, AIMessage))

&#x20;   outputs = \[]

&#x20;   for tc in last\_ai.tool\_calls:

&#x20;       result = execute\_tool(tc\["name"], tc\["args"])   # 复用你阶段 1 的 execute\_tool！

&#x20;       outputs.append(ToolMessage(content=str(result), tool\_call\_id=tc\["id"]))

&#x20;   return {"messages": outputs}

\# ---- 条件路由：继续还是结束 ----

def should\_continue(state: AgentState) -> str:

&#x20;   last = state\["messages"]\[-1]

&#x20;   if getattr(last, "tool\_calls", None):

&#x20;       return "call\_tools"

&#x20;   if state\["iterations"] >= state\["max\_iterations"]:

&#x20;       return END

&#x20;   return END

\# ---- 组装成图 ----

builder = StateGraph(AgentState)

builder.add\_node("call\_model", call\_model)

builder.add\_node("call\_tools", call\_tools)

builder.add\_edge(START, "call\_model")

builder.add\_conditional\_edges("call\_model", should\_continue)

builder.add\_edge("call\_tools", "call\_model")   # 工具执行完 → 回到 LLM，形成循环

graph = builder.compile()

\# ---- 运行 ----

def run\_agent(user\_message, session\_id="default\_session"):

&#x20;   history = load\_history(session\_id)

&#x20;   result = graph.invoke({

&#x20;       "messages": \[SystemMessage(content="You are a helpful assistant."), \*history,

&#x20;                    HumanMessage(content=user\_message)],

&#x20;       "iterations": 0,

&#x20;       "max\_iterations": 5,

&#x20;   })

&#x20;   return result\["messages"]\[-1].content
```

### 4.3 手写版 vs LangGraph 版：逐行对照



| 手写版做的事                               | LangGraph 版的对应                                        | 收益           |
| ------------------------------------ | ----------------------------------------------------- | ------------ |
| `for i in range(max_iterations)`     | 图上的 `call_tools → call_model` 循环边 + `should_continue` | 循环结构显式化、可视化  |
| `if not response.tool_calls: return` | `should_continue` 条件边返回 `END`                         | 退出逻辑变成数据，可审计 |
| 手动 `messages.append(...)`            | `add_messages` Reducer 自动合并                           | 不再手写拼接代码     |
| `logger.info` 打印                     | `graph.stream()` / LangSmith 追踪                       | 每步输入输出可观测    |
| 状态困在局部变量                             | State 是显式数据结构                                         | 可持久化（§5）、可恢复 |

> **对照你的项目**
>
> ：迁移时
>
> **复用**
>
> 你阶段 1 的 
>
> `execute_tool`
>
>  和阶段 2 的 
>
> `load_history`
>
> /
>
> `save_message`
>
> ——LangGraph 只管 "怎么编排"，"怎么执行工具"" 怎么存对话 "还是你自己的代码。这是" 渐进式重构 "，不是推倒重来。



***

## 5. Checkpoint：让 Agent"死不了" 的机制

### 5.1 是什么

**Checkpoint（检查点）** 是 LangGraph 的内置持久化机制：**图的每一步执行完，都把完整 State 快照存下来**。



```
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()                      # 存内存（重启丢）

graph = builder.compile(checkpointer=checkpointer)

\# 关键：thread\_id —— 给"同一场会话"一个钥匙

config = {"configurable": {"thread\_id": session\_id}}

graph.invoke({...}, config=config)                # 第一次执行，状态存入 checkpoint
```

### 5.2 为什么需要它

解决 §1 的**痛点 2**：进程崩溃 / 服务重启 / 网络中断后，**从断点继续**，而不是从头再来。同一把 `thread_id`，第二次 `invoke` 会自动接着上一次的状态走。

### 5.3 底层原理



* **Checkpoint 里存了什么**：这个 `thread_id` 到目前为止的**完整 State 快照**（所有消息 + 所有自定义字段），每个超步存一份。

* **thread\_id 是什么**：一个字符串，就是 "哪一场会话" 的钥匙。**同一个 thread\_id = 共享记忆；不同 thread\_id = 彼此隔离**（就跟你阶段 2 的 `session_id` 一个道理）。

* **可插拔后端**：`MemorySaver`（内存，演示用）→ `SqliteSaver`（SQLite，本地持久化）→ `PostgresSaver`/`RedisSaver`（生产）。



```
from langgraph.checkpoint.sqlite import SqliteSaver

with SqliteSaver.from\_conn\_string("checkpoints.sqlite") as checkpointer:

&#x20;   graph = builder.compile(checkpointer=checkpointer)

&#x20;   # 服务重启后，同一 thread\_id 仍能继续
```

### 5.4 重要辨析：Checkpoint vs 你阶段 2 的 SQLite 记忆

这是阶段 3 最容易混淆的概念，务必分清：



|      | 阶段 2 SQLite 记忆（你自己写的） | 阶段 3 LangGraph Checkpoint  |
| ---- | --------------------- | -------------------------- |
| 存什么  | 对话历史（messages 表）      | **整个执行状态**（State 快照，含中间结果） |
| 解决什么 | "下一轮对话还记得上文"          | "执行到一半也能断点续传"              |
| 谁在管  | 你自己写的 `save_message`  | LangGraph 框架自动             |
| 粒度   | 一次消息一条记录              | 每个超步一份快照                   |

**两者互补，不是替代**：SQLite 存的是 "**用户视角的历史**"（聊天记录），Checkpoint 存的是"**引擎视角的状态**"（执行过程）。真实生产里，对话历史放业务数据库（你的 MySQL），执行状态放 Checkpoint（LangGraph 管）。

> **对照你的项目**
>
> ：等做阶段 2+（MySQL + Redis）时，你会看到：
>
> `thread_id`
>
>  完全可以复用你的 
>
> `session_id`
>
> ；Checkpoint 后端可以换成 RedisSaver—— 你阶段 2+ 学的 Redis 缓存、阶段 3 学的 Checkpoint，在这里
>
> **会师**
>
> 。



***

## 6. 流式输出（SSE）—— 打字机效果

### 6.1 是什么

**SSE（Server-Sent Events，服务器发送事件）**：服务器通过一个 HTTP 长连接，**持续**往客户端推送数据（不是一次性返回完整响应）。你看到 ChatGPT 那种 "一个字一个字蹦出来" 的打字机效果，就是这么来的。

### 6.2 为什么需要它



* **体验**：长任务让用户干等 10 秒 vs 边生成边显示，体验天差地别。

* **反馈**：能实时看到 Agent 走到哪一步了（"正在调用工具…"）。

### 6.3 底层原理



* **普通 HTTP 响应**：请求 → 服务器处理完 → 一次性返回全部 → 连接关闭。

* **SSE**：请求 → 服务器**保持连接** → 不断发送 `data: 内容\n\n` → 直到结束。媒体类型是 `text/event-stream`。

* **和 WebSocket 的区别**：SSE 是**单向**（服务器→客户端）、基于普通 HTTP、自动重连；WebSocket 是**双向**、需要额外握手协议。SSE 对 "服务器往客户端流式推内容" 这种场景**更简单够用**。

### 6.4 代码：LangGraph 流式 + FastAPI 对接



```
\# LangGraph 侧：用 stream() 而不是 invoke()

for chunk in graph.stream(input, config=config, stream\_mode="updates"):

&#x20;   for node\_name, update in chunk.items():

&#x20;       if node\_name == "call\_model":

&#x20;           # 这里拿到模型的一步输出，可转发给前端

&#x20;           pass

\# FastAPI 侧：用 StreamingResponse 包成 SSE

from fastapi.responses import StreamingResponse

@app.post("/api/chat/stream")

async def chat\_stream(request: ChatRequest):

&#x20;   def event\_generator():

&#x20;       for chunk in graph.stream(input, config, stream\_mode="updates"):

&#x20;           yield f"data: {chunk}\n\n"     # SSE 协议格式

&#x20;   return StreamingResponse(event\_generator(), media\_type="text/event-stream")
```

> **对照你的项目**
>
> ：阶段 3 的验收标准是 "实现 SSE 流式输出"。你可以先不改现有 
>
> `/api/chat`
>
> ，
>
> **新增**
>
> 一个 
>
> `/api/chat/stream`
>
>  端点，用浏览器 
>
> `fetch`
>
>  或 
>
> `curl -N`
>
>  测试流式效果，和原来的普通接口对比体验。



***

## 7. 常见坑 & 自测

### 常见坑



1. **节点返回整个 State**（`return {**state, ...}`）→ 错！只返回**要更新的字段**。

2. **忘了 Reducer** → 节点返回 `{"messages": [新消息]}` 把旧消息覆盖没了。消息字段必须用 `Annotated[list, add_messages]`。

3. **thread\_id 不传 / 每次都变** → 图没有记忆，每次都是全新会话（和 `session_id` 一样）。

4. **条件边返回的字符串不是任何节点名**（也不是 `END`）→ 运行时报错。要么返回确切的节点名，要么用映射字典 `{"tools": "call_tools", END: END}`。

5. `getattr(last, "tool_calls", None)`**&#x20;判断工具调用** → 不同版本消息对象字段名可能不同，用 `hasattr`/`getattr` 兜底。

### 自测（每题先自己想，再动手验证）



1. 解释 Reducer 的作用，并说出 `add_messages` 和默认覆盖的区别。

2. 手写的 `for` 循环在 LangGraph 里对应哪两条边？

3. 为什么 `should_continue` 是 "函数" 而不是 "固定的边"？

4. Checkpoint 和 SQLite 对话历史，各自解决什么问题？

5. `thread_id` 不传会发生什么？为什么？

6. 用 `graph.get_graph().draw_mermaid_png()` 把你的图导出成图片，能 "看到" 循环在哪里吗？



***

## 8. 本阶段验收对照 & 下一步

对应计划文档阶段 3 的验收标准：



* [ ] Agent 运行逻辑完全由 LangGraph 管理（不再用 `for` 循环手写）

* [ ] 演示一次 "中断后的 Checkpoint 恢复"（进程崩了，同一 thread\_id 继续）

* [ ] 实现 SSE 流式输出（`/api/chat/stream`）

**做完主线的进阶思考（可选）**：用 llamaIndex 的 `Workflow`（Event/Step）把你这个 "调工具循环" 复刻一遍 —— 感受 LangGraph 的 "图驱动" 和 llamaIndex 的 "事件驱动" 差别（这就是我们说的 "对照 demo"）。感受点记进对比笔记：节点 / 边 vs 事件 / 步骤、State 管理 vs Context 传递。

**下一步**：阶段 3 完成后进入阶段 4（RAG 知识库，用 Chroma + Embedding）—— 那时你会看到 LangChain 生态自带的 RAG 组件，和阶段 4 学习文档里的 llamaIndex 路线做个对照。