# 阶段 0 学习文档：FastAPI 与 LLM 最小闭环

> 适用阶段：阶段 0｜V0 最小 Agent（约 1 天）
> 技术栈：FastAPI、Pydantic、OpenAI SDK、环境配置
> 配套项目代码：
>
> `app/config.py`
>
> 、
>
> `app/llm.py`
>
> 、
>
> `app/main.py`
>
> 、
>
> `app/schema.py`



***

## 0. 这份文档怎么用

阶段 0 是你项目的 "地基"：一个能通过 HTTP 接口调用大模型的**最小闭环**。写这份文档时你可能已经把它敲出来了，所以目标不是 "怎么敲"，而是把你敲的每一行背后的**为什么**补上。读完你要能回答：



1. FastAPI 和 "写一个 Python 脚本" 差在哪？

2. Pydantic 到底在保护什么？

3. 一次 HTTP 请求在你这套代码里走过了哪些关卡？



***

## 1. 阶段 0 做了什么：一个最小闭环

你的 V0 长这样：



```
用户发来一句 "你好"

&#x20;  │  HTTP POST /api/chat

&#x20;  ▼

FastAPI 接收 → Pydantic 校验 → run\_agent/chat → 调 DeepSeek API

&#x20;  │                                            │

&#x20;  │              LLM 返回文本                    │

&#x20;  ▼                                            ▼

返回 JSON {"reply": "..."} ←────────────────────┘
```

**"最小闭环" 的含义**：从 "用户的输入" 到 "模型的输出"，数据完整地走了一圈。这个闭环是所有后续阶段的地基 —— 阶段 1 在闭环中间加 "工具"，阶段 2 在闭环前后加 "记忆"，但骨架都是这条链路。



***

## 2. FastAPI：为什么选它

### 是什么

FastAPI 是一个现代 Python Web 框架，用来 "把 Python 函数变成可以被外部访问的 HTTP 接口"。

### 为什么用它（对比其他方案）



| 方案            | 特点                  | 为什么不用在个人项目       |
| ------------- | ------------------- | ---------------- |
| 纯脚本 `input()` | 只能本机、人肉交互           | 没法给外部 / 前端调用     |
| Flask         | 简单、老牌               | 请求参数校验全靠手写，异步支持弱 |
| Django        | 全家桶、重               | 个人小项目过度设计        |
| **FastAPI**   | 类型提示驱动、自动校验、自动文档、异步 | ✅ 正好是个人项目的甜点区    |

FastAPI 的三大核心卖点，每一个都在你的代码里体现：



1. **类型提示驱动的自动校验**——`request: ChatRequest` 写了类型，FastAPI 就自动帮你解析 JSON、校验字段、类型转换，不用写一行手动的 `if "message" not in body`。

2. **自动交互文档**—— 服务跑起来后访问 `http://127.0.0.1:8000/docs`，能看到可点击调试的 API 页面（FastAPI 基于 OpenAPI 规范自动生成）。

3. **异步支持**——`async def` 端点不阻塞事件循环，高并发下表现好。



***

## 3. Pydantic：数据的 "守门员"

### 是什么

Pydantic 是一个**数据校验与序列化**库，用 Python 类型注解声明 "数据长什么样"，它自动完成校验和转换。

### 为什么需要它（为什么不能裸用 dict）

Web 边界上**你收到的所有数据都不可信**：可能是缺字段、类型错、多字段、格式错。你的 `ChatRequest` 就说明了这个理念：



```
class ChatRequest(BaseModel):

&#x20;   message: str

&#x20;   session\_id: str = "default\_session"   # 有默认值 = 可选
```



* 请求没带 `message` → Pydantic 自动报 422 错误（字段缺失）

* 请求带 `message: 123`（数字）→ 报类型错误

* 带了 `session_id` → 用请求的值；没带 → 用默认值 `"default_session"`

### 底层原理

Pydantic 不是 "if 判断"，它做的是：**根据类型注解生成一个校验器，把输入数据校验并转换成目标类型的实例**（2.0 之后基于 Rust 核心 `pydantic-core`，性能很高）。核心价值是 \*\*"声明式校验"\*\*—— 你用类型声明规则，而不是用代码堆规则。

> **对照你的项目**
>
> ：你 
>
> `schema.py`
>
>  里的 
>
> `ChatResponse`
>
>  定义了返回结构 
>
> `reply: str`
>
> ，配合 
>
> `main.py`
>
>  的 
>
> `response_model=ChatResponse`
>
> ，FastAPI 会保证
>
> **返回给用户的一定是合法的 JSON**
>
> —— 这就是 "守门员" 管进也管出。



***

## 4. HTTP 基础：一次请求的一生

在继续之前，把 HTTP 的关键心智模型补上（后面阶段一直要用）：



```
客户端(浏览器/curl)         服务器(FastAPI)

&#x20;    │  GET /health             │

&#x20;    │ ────────────────────────► │ 路由匹配 → 执行函数

&#x20;    │                           │

&#x20;    │ 200 OK + JSON            │

&#x20;    │ ◄──────────────────────── │
```



* **路由**：`@app.get("/health")` 就是 "当有人 GET /health，就执行这个函数"。

* **GET vs POST**：GET 用于 "读"（无副作用），POST 用于 "提交 / 创建"（有副作用，数据放 body）。你让 LLM 干活会改变服务器状态 → 用 POST `/api/chat`。

* **状态码**：200 成功、400 参数错误、404 找不到、500 服务器内部错误。FastAPI 校验失败返回 422（请求体不合法）。



***

## 5. 调 LLM：OpenAI SDK + DeepSeek

### 你的 `chat()` 到底在干嘛



```
response = client.chat.completions.create(

&#x20;   model=setting.llm\_model,        # 用哪个模型

&#x20;   messages=\[                      # 对话内容（最重要）

&#x20;       {"role": "system", "content": "You are a helpful assistant."},

&#x20;       {"role": "user", "content": message}

&#x20;   ],

)

return response.choices\[0].message.content
```

### messages 协议（一定要吃透）

大模型的对话 API 接受一个**消息列表**，每条消息有三个角色：



| role        | 含义           | 典型用法                           |
| ----------- | ------------ | ------------------------------ |
| `system`    | 设定助手的行为 / 人设 | "You are a helpful assistant." |
| `user`      | 用户说的话        | 用户的输入                          |
| `assistant` | 助手之前说的话      | 多轮对话的历史（阶段 2 重点）               |

**关键认知**：大模型是**无状态**的 —— 它每次只看到你这次传的 `messages`。它 "记住" 上一轮，是因为你把上一轮的 `assistant` 消息也传进去了。这个 "无状态 + 显式传历史" 的心智模型，是后面阶段 2（记忆）的根源。

### 为什么用 SDK 而不是直接发 HTTP

OpenAI SDK 帮你处理了：HTTP 请求、鉴权头、错误解析、重试、类型化响应对象（`response.choices[0].message`）。你的 `chat_with_tools` 以后还要传 `tools` 参数，SDK 让这些复杂协议变成 Python 参数。

### base\_url 的妙用：同一套 SDK 换服务商



```
client = OpenAI(api\_key=setting.llm\_api\_key, base\_url=setting.llm\_base\_url)
```

你用的 DeepSeek 提供了**OpenAI 兼容接口**，所以只要把 `base_url` 指向 DeepSeek，就能用 OpenAI SDK 调 DeepSeek 模型。这就是 "协议兼容" 的力量 ——**你学的是一套通用协议，不绑定任何一家厂商**。



***

## 6. 配置管理：pydantic-settings + .env

### 为什么不能把 API Key 硬编码在代码里



* **安全**：API Key 是密钥，硬编码 = 提交到 Git = 泄露（别人能看到、盗用你账户花钱）。

* **可移植**：换台机器、换个模型，不用改代码。

* **12-Factor 原则**：配置应该由**环境**提供，而不是写死在代码里。

### 你的 Settings 类



```
class Settings(BaseSettings):

&#x20;   llm\_api\_key: str

&#x20;   llm\_base\_url: str

&#x20;   llm\_model: str

&#x20;   model\_config = SettingsConfigDict(env\_file=".env", extra="ignore")
```



* 从 `.env` 文件读取（`env_file=".env"`），也支持系统环境变量。

* `extra="ignore"`：.env 里多出来的无关变量不报错，容错。

* `setting = Settings()` 模块级单例：全局共用一份配置。

> **对照你的项目**
>
> ：
>
> `.env`
>
>  已被 
>
> `.gitignore`
>
>  排除（记得确认），
>
> `config.py`
>
>  只在模块导入时读一次 —— 密钥不进仓库、配置集中管理，这就是 "生产级" 的最基本素养。



***

## 7. 启动与调试：uvicorn



```
uvicorn app.main:app --reload --port 8000
```



* `app.main:app` = 模块 `app.main` 里的 `app` 对象。

* `--reload` = 代码改动自动重启（开发利器，**但生产环境绝不能开**）。

* 启动后：`http://127.0.0.1:8000/health` 探活，`/docs` 看交互文档。



***

## 8. 对照你的代码（阶段 0 全链路）



| 文件              | 职责        | 你写它的 "为什么"                  |
| --------------- | --------- | --------------------------- |
| `app/config.py` | 读配置       | 密钥不进代码，换模型不改代码              |
| `app/llm.py`    | 封装 LLM 调用 | 把 "调模型" 收敛成一个函数，别处不裸调 SDK   |
| `app/schema.py` | 请求 / 响应结构 | 声明式校验，管进管出                  |
| `app/main.py`   | HTTP 入口   | 把 Agent 能力暴露成 API，可被任何客户端调用 |
| `.env`          | 密钥 / 模型配置 | 12-Factor 环境配置              |



***

## 9. 常见坑 & 自测

### 常见坑



1. `.env` 忘了加进 `.gitignore` → 密钥被推上 GitHub（泄露风险）。

2. `messages` 只传 `user` 不传 `system` → 模型没有 "人设"，回答随意。

3. 响应解析写错层级：正确是 `response.choices[0].message.content`，不是 `response.content`。

4. 端口被占 → 换 `--port` 或杀掉占用进程（你之前遇到的过程序没热更新就是这个原因）。

### 自测



1. 用自己的话解释：为什么大模型是 "无状态" 的？"记忆" 从哪来？

2. `GET` 和 `POST` 的使用场景分别是什么？`/api/chat` 为什么用 POST？

3. 如果客户端发来 `{"message": 123}`（数字），会发生什么？谁拦下来的？

4. 把 `base_url` 改成其他 OpenAI 兼容服务商（比如硅基流动、通义），需要改哪些代码？

5. 打开 `http://127.0.0.1:8000/docs`，说出这个页面是怎么 "自动生成" 的。



***

**下一步**：阶段 0 的闭环已经跑通，阶段 1 要在这个闭环中间插入 "工具调用"—— 大模型不再只是 "回话"，而是能 "动手干活"。见 `阶段1_ToolAgent工程化.md`。