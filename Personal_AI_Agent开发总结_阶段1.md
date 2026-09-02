# Personal AI Agent 项目开发总结

> 覆盖范围：从项目初始化（V0）到 **阶段 1 · V1 工程化 Tool Agent 完成**（阶段 1 收尾于 2026-09-01）
> 本文档结合 `01.docx`（V0→V1 前期总结）与阶段 1 收尾（2026-09-01）的完整开发过程整理而成。

---

## 一、项目演进总览

整个项目从零到"可扩展的 Tool Agent 框架"，走过了这样一条技术栈链路：

```
Python / FastAPI
   ↓
LLM 基础调用（OpenAI-compatible API）
   ↓
Calculator Tool（安全计算）
   ↓
AST 安全计算（替换 eval）
   ↓
pytest 测试
   ↓
Tool Schema / Tool Registry / Tool Executor
   ↓
LLM Tool Calling
   ↓
Agent Loop
   ↓  【阶段 1 收尾完成】
可扩展工具框架（BaseTool + 动态注册表 + 通用执行器 + 有界循环 + mock 测试 + 日志）
```

现在项目不再是简单的 `用户 → LLM → 回复`，而是真正意义上的：

```
用户 → Agent → LLM → 决定是否调用工具 → Tool Call → Tool Executor → 工具执行
      → Tool Result → 重新交给 LLM → 最终回答
```

---

## 二、第一阶段（V0 → V1 前期）已完成内容 —— 源自 01.docx

### 2.1 环境与工程基础

| 问题 | 根因 | 解决方案 / 经验 |
|---|---|---|
| 多个 Python 并存（Anaconda / Python313 / WindowsApps） | 不同命令可能使用不同解释器 | 用 `py -0p` 确认版本；用 `where.exe python` 查清 |
| `pytest` 用的是 Anaconda 环境 | 全局 pytest 与项目 .venv 不一致 | 一律用 `python -m pytest`，保证解释器与 pytest 一致 |
| Git 仓库初始位置错误（`C:\Users\fff`） | 把整个用户目录当成了仓库 | 修正到 `personal_agent/`；新项目先查 `git rev-parse --show-toplevel` |
| `No matching distribution found for pydantic-setting` | 包名拼写错误 | 正确的是 `pydantic-settings` |

### 2.2 后端与 LLM 基础

- **FastAPI 404**：`GET /` 404 不代表服务器挂了，而是没有定义该路由；真实接口是 `/api/chat`。看到 `Application startup complete` 即已启动。
- **LLM 返回 None 导致 500**：`chat()` 没写 `return response.choices[0].message.content`，函数默认返回 `None` → Pydantic 校验失败 → 500。经验：500 要顺着数据流排查（请求 → 业务函数 → LLM → 返回值 → Pydantic → HTTP Response）。
- **理解 Tool Calling**：普通调用 `user → LLM → content`；工具调用是 `user → LLM → tool_calls → 程序执行 → tool result → LLM → content`。此时 `message.content` 可能是 None，真正要看的是 `message.tool_calls`。
- **json.load vs json.loads**：`json.load()` 用于文件对象，`json.loads()` 用于字符串。`tool_call.function.arguments` 是字符串，必须用 `loads`。

### 2.3 Calculator 安全计算

- **最初用 `eval(expression)` + 字符白名单** → 不安全（eval 可执行任意 Python 表达式，而 Calculator 会被 LLM 调用）。
- **升级为 AST 安全计算**：`ast.parse(expression, mode="eval")`，只允许安全节点（`ast.Expression / Constant / BinOp / Add / Sub / Mult / Div / Pow / Mod / FloorDiv / UnaryOp / USub / UAdd`），自己递归计算。
- 支持：`+ - * / ** // %`、正负号、括号、除零检测、非法表达式检测、类型检测、数字范围限制（`max_number = 1000000000`）。
- 非法表达式通过异常处理转成可读错误信息，而不是让程序崩溃。

### 2.4 pytest 与模块导入

- **测试机制**：`test_*.py` / `*_test.py` 文件、`def test_xxx()` 函数、`assert` 判断、pytest 自动发现。
- **模块导入**：`from tools.calculator import calculate` 必须用项目根目录下的**绝对导入**，不能 `from .tools...`（相对导入无父包会报错）；从项目根执行 `python -m pytest`。

### 2.5 Tool Schema 与 Agent Loop

- **Tool Schema**：`{type: function, function: {name, description, parameters}}`，注意 JSON 语法（字段逗号、`"required"` 加引号）。
- **第一次 Tool Calling 成功**：LLM 返回 `tool_calls=[Function(arguments='{"expression": "1+1"}', name='calculator')]`，证明 LLM 理解意图、选对工具、生成参数。
- **Agent Loop 400 错误**：`chat_with_tools("请计算1+1")` 传了字符串，Chat Completions 要求 `messages` 是列表。解决：Agent 自己维护 `messages` 列表。
- **Agent Loop 核心**：LLM → 有 tool_calls？无则返回 content；有则保存 assistant tool_call → 执行 Tool → 保存 tool result → 再次调用 LLM（循环）。

### 2.6 四层能力划分（截至 V1 前期）

| 层级 | 内容 |
|---|---|
| Backend | Python / FastAPI / Pydantic / 环境配置 |
| LLM | OpenAI-compatible API / 调用 / messages / response |
| Tool | Calculator / AST / Tool Schema / Tool Registry / Tool Executor |
| Agent | Tool Calling / Tool Result / messages 状态 / Agent Loop |

**最大进步**：理解了 Agent 是什么——能区分 LLM、Tool、Tool Schema、Tool Call、Tool Executor、Tool Result、Message、Agent Loop 各自负责什么。这条链路就是后面学 LangGraph 的基础。

---

## 三、阶段 1 收尾（2026-09-01）完成的内容

按重构版计划的依赖顺序，完成了从"写死 Calculator 的 Agent"到"可扩展 Agent 框架"的升级：

### 3.1 统一工具接口 `tools/base.py`

设计 **`BaseTool` 基类**，每个工具自带 4 个要素：

| 成员 | 作用 |
|---|---|
| `name` | 工具唯一标识，LLM 和程序靠它路由 |
| `description` | 一句话功能描述，给 LLM 判断何时调用 |
| `parameters` | JSON Schema 参数定义，给 LLM 生成参数 |
| `run(**kwargs)` | 真正业务逻辑，子类重写 |
| `execute(arguments)` | **统一入口**：解析参数 + 调 run + 异常兜底（返回字符串） |
| `schema()` | 生成 OpenAI function-calling 格式 |

**关键设计**：`execute` 把参数解析（JSON 字符串 / dict 双分支）和异常处理（`ZeroDivisionError / TypeError / Exception` 全部转成可读字符串）统一在基类，子类只管 `run`。错误**返回**字符串给 LLM 看，而不是**抛出**让程序崩。

### 3.2 工具类

- `CalculatorTool`：`name="calculator"`，参数 `expression`（string，必填），`run` 调用原 `calculate`。
- `GetCurrentTimeTool`（`tools/time_tool.py`）：`name="get_current_time"`，无参数（`properties={}`），`run` 返回当前时间。

### 3.3 动态注册表 `tools/registry.py`

- `register_tool(tool)`：把工具实例存入 `Tools`，同时自动 `append(tool.schema())` 到 `TOOL_SCHEMA`。
- 清理了导入副作用（原来模块加载时就执行 `Tools["calculator"]("1+1")`）。
- **可扩展性验证**：新增 time 工具只做了"写类 + 注册一行"，`agent.py / executor.py / llm.py` 零改动，LLM 通过自动更新的 TOOL_SCHEMA 自动认识新工具。

### 3.4 通用执行器 `tools/executor.py`

`execute_tool(tool_name, arguments)`：
- 从 `Tools.get(tool_name)` 取工具实例，找不到返回 `"找不到工具 xxx"`；
- 找到则 `tool.execute(arguments)`（解析和异常全部下沉到基类，executor 只负责路由）。

### 3.5 Agent Loop 加固 `app/agent.py`

- `run_agent(user_message, max_iterations=5)`：`for ... range(max_iterations)` 替代 `while True`，**给 LLM 决策预算，防死循环**；循环结束未返回时给兜底消息。
- 从 `tool_call` 里拆出 `function.name` 和 `function.arguments` 传给 `execute_tool`（executor 与 OpenAI 对象解耦）。
- 加日志：记录用户消息、每轮迭代、调用的工具与参数、达到上限告警。

### 3.6 FastAPI 接入 `app/main.py`

- `/api/chat` 从 `chat()`（普通 LLM）切换到 `run_agent()`（带工具的 Agent）。
- 修复 `health` 缺前导斜杠的历史 bug（`@app.get("/health")`）。

### 3.7 测试套件整理与 mock 测试

- 修 `test_tools.py`：旧写法 `Tools["calculator"]("...")`（实例不可调用）→ 改为走 `execute_tool` 全链路，断言字符串结果。
- 过时的演示脚本（`test_executor.py`、`test_tools_call.py`）移到 `demo/` 并改名（`demo_executor.py`、`demo_tools_call.py`）。
- 重写 `test_agent.py`：用 `MagicMock` + `patch` 模拟 LLM，测试 Agent 循环逻辑不依赖真实 API。
- **最终 14 个测试离线全绿**。

### 3.8 Git 收尾

- 清理临时文件、重写 `.gitignore`（排除 .env / .venv / __pycache__ / *.docx / copy.ipynb），两个 commit，工作区干净。

---

## 四、阶段 1 收尾过程中遇到的所有问题与解决（重点）

### 4.1 设计类问题

**① BaseTool 的 `execute` 用 `raise` 还是 `return`？**
- 错误：把异常 `raise ValueError` 抛出去。
- 原因：设计目标是"工具出错不能让 Agent 循环崩溃"，错误要转成字符串**返回**，LLM 才能"看到"并决定重试或解释。
- 解决：三处 `raise` 全部改 `return` 可读错误字符串。
- 经验：**错误信息是给谁看的？** 在 Agent 链路里是给 LLM 看的，所以必须可读、可返回。

**② 用 Pydantic 后子类覆盖字段报错**
- 报错：`Field 'name' defined on a base class was overridden by a non-annotated attribute`。
- 原因：Pydantic v2 要求子类覆盖字段时**必须带类型注解**。
- 解决：子类属性都写成 `name: str = "..."`、`parameters: dict = {...}`。
- 经验：用 Pydantic 的代价——字段定义必须规范；它靠注解做校验。

**③ `parameters` 写成普通 dict，不是 JSON Schema**
- 错误：`parameters = {"expression": "要计算的算数表达式"}`。
- 原因：LLM 需要知道"参数是一个对象、有 expression 字段、类型 string、必填"。
- 解决：`{"type": "object", "properties": {"expression": {"type": "string", ...}}, "required": ["expression"]}`。
- 判断标准：LLM 能否凭这个 dict 知道"该传什么、什么类型、必不必须"。

**④ 把 `name` / `description` 误塞进 `parameters`**
- 错误：无参数工具的 `parameters` 里嵌套写了 name/description。
- 原因：混淆了"工具是什么"（name/description，在类属性）与"工具要什么参数"（parameters）。
- 解决：无参数工具 `parameters = {"type": "object", "properties": {}}`。
- 经验：`parameters` 只描述真实参数，否则 LLM 会以为要传多余参数。

**⑤ `max_iterations` 失效（while 套 for）**
- 错误：`while True:` 外层套 `for i in range(max_iterations):`。
- 原因：for 跑完又回到 while True 开始新一轮，死循环风险仍在，max_iterations 没生效。
- 解决：删 `while True`，只留 `for ... range(max_iterations)`，循环结束加兜底 `return`。
- 经验：`for + range` 本身就是有界循环，`while True` 是无限；二选一。

**⑥ 变量名误导 + 实例不可调用**
- 错误：`tool_name = Tools.get(...)`（变量名是 tool_name，存的却是实例）；`tool_name(...)` 直接调用实例报 `'CalculatorTool' object is not callable`。
- 解决：变量命名贴合真实内容（`tool`）；调用实例方法 `tool.execute(arguments)`。

### 4.2 导入 / 模块类问题

**⑦ `from base import BaseTool` 报 `No module named 'base'`**
- 原因：`base.py` 在 `tools/` 包里，导入路径必须对齐目录结构。
- 解决：`from tools.base import BaseTool`（绝对导入，带包名）。

**⑧ test_check.py 放错目录**
- 错误：临时验证脚本放到了 `test/` 子目录，在根目录运行时找不到文件。
- 解决：放到项目根目录运行（保证 `from tools.executor import ...` 能以项目根为 sys.path 起点）。

### 4.3 FastAPI / 接口类问题

**⑨ 接口 500：response_model 字段不匹配**
- 错误：`ChatResponse` 定义 `answer` 字段，接口返回 `{"reply": ...}`。
- 原因：`response_model` 是响应契约，FastAPI 会拿模型校验返回值，字段对不上抛 `ResponseValidationError` → 500。
- 解决：统一字段名（改返回为 `{"answer": reply}` 或改模型为 `reply`）。
- 经验：**response_model 是 API 对外的响应契约**，返回字典必须完全匹配模型字段。

**⑩ 接口 500：uvicorn 旧进程没热更新**
- 原因：改完代码但服务器进程还跑着旧版本，`--reload` 不一定每次都触发。
- 解决：`Ctrl+C` 彻底停掉服务，重新启动，确认 `Application startup complete` 再测。
- 经验：改了代码还报旧错误，先怀疑"服务是不是没加载新代码"。

**⑪ `async def` 里调同步 `run_agent`**
- 提示：`run_agent` 是同步阻塞函数，在 `async def` 里直接调会阻塞事件循环；改普通 `def` 更合适（FastAPI 自动放线程池）。非 bug，但值得注意。

### 4.4 命令行 / 环境类问题

**⑫ PowerShell 里 `curl` 不是 curl**
- 错误：用 `curl -H ... -d ...`，报"无法绑定参数 Headers"。
- 原因：PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，语法完全不同（不认 `-H`/`-d`）。
- 解决：用 `curl.exe`（真 curl）或 `Invoke-RestMethod -Uri ... -Method Post -ContentType "application/json" -Body '{"message": "..."}'`。

**⑬ PowerShell 双引号转义损坏 JSON**
- 错误：`-d "{\"message\": \"...\"}"` 的 JSON body 被拆碎，服务器收到空对象，甚至 URL 报 `Malformed input`。
- 原因：PowerShell 的双引号转义规则与 Unix shell 不同。
- 解决：用 `Invoke-RestMethod` + 单引号包 JSON（单引号内双引号是字面量，零转义）。

**⑭ 日志中文乱码（`??? 1+1`）**
- 原因：Windows 控制台 GBK 编码与 UTF-8 冲突。
- 影响：仅显示问题，不影响功能。可忽略或用 `chcp 65001` / 设置编码解决。

### 4.5 测试类问题

**⑮ pytest 收集规则：移到 demo/ 还是被收集**
- 错误：把演示脚本移到 `demo/` 后 pytest 仍收集执行它们。
- 原因：pytest 按**文件名**模式收集 `test_*.py` / `*_test.py`，与目录无关；`demo/test_executor.py` 以 test_ 开头照样被收集。
- 解决：改名为 `demo_executor.py`、`demo_tools_call.py`（去掉 test_ 前缀）。

**⑯ 测试断言与实现不一致**
- 错误：测试断言 `"我无法处理你的请求，请重新尝试。"`，而 `agent.py` 返回 `"达到最大迭代次数，未能得到最终答案。"`。
- 解决：统一文案（推荐实现返回用户友好文案，测试跟着走）。

**⑰ mock 测试暴露真实 bug**
- 现象：mock 测试报 `AttributeError: 'list' object has no attribute 'name'`。
- 根因：`agent.py` 日志写了 `response.tool_calls.name`，但 `tool_calls` 是**列表**，`name`/`arguments` 在列表**每个元素**上。
- 解决：遍历 `for tool_call in response.tool_calls`，取 `tool_call.function.name`。
- 经验：**mock 测试的价值**——让 Agent 循环离线完整跑通，在真实 API 调用之前揪出 bug；这个 bug 真实调用时也会崩。

### 4.6 Git / 文件类问题

**⑱ .gitignore 的 BOM 与换行拼接**
- 错误：`Add-Content` 追加时原文件末尾无换行，导致 `app/copy.ipynb` 拼到上一行末尾（`...docxapp/copy.ipynb`），规则失效。
- 错误：`Set-Content -Encoding UTF8` 写入带 BOM（`﻿.env` 开头），与已提交版本产生编码差异。
- 解决：用 Python 无 BOM 重写 `.gitignore`（`open(..., encoding='utf-8', newline='\n')`），内容干净、规则独立成行。

---

## 五、当前系统架构

```
/api/chat (FastAPI, response_model=ChatResponse)
   └─ run_agent(message, max_iterations=5)   ← 有界循环 + 日志
        └─ chat_with_tools(messages)         ← LLM 决定是否调工具
        └─ execute_tool(name, arguments)     ← 通用执行器（只管路由）
             └─ Tools 注册表                 ← register_tool() 自动收集 schema
                  ├─ CalculatorTool
                  └─ GetCurrentTimeTool
        └─ 测试：14 passed（含 mock LLM，离线可跑）
        └─ Git：2 commits，工作区干净
```

**核心设计原则（已落地）**：
1. **开闭原则**：加工具 = 写类 + 注册一行，其他层零改动。
2. **职责分层**：executor 只管路由，base 只管执行和兜底，agent 只管编排。
3. **错误可见性**：工具错误返回字符串给 LLM，而非抛出崩溃。
4. **决策预算**：max_iterations 限制 Agent 循环，防死循环。
5. **可观测性**：日志记录每一轮决策。
6. **可测试性**：mock 外部依赖，测试离线、稳定、不花钱。

---

## 六、阶段 1 收尾的核心收获

1. **面向接口/框架设计**：亲手验证了"注册即用"的可扩展框架——加第二个工具时 agent/executor 一行没改。
2. **异常处理的哲学**：错误信息是给谁看的？在 Agent 链路里给 LLM 看，所以返回可读字符串。
3. **Pydantic 的严谨**：类型注解是校验系统的基石，字段定义必须规范。
4. **测试意识**：区分真测试 vs 演示脚本；mock 让测试不依赖真实 API；测试能在真实调用前抓 bug。
5. **调试方法论**：500 不猜，复现 + 看 traceback + 定位根因；改了代码先怀疑"服务是否加载新版本"。
6. **工程习惯**：日志（可观测）、git 提交（可回溯）、.gitignore（干净边界）。

---

## 七、当前进度与下一步

**当前进度**：阶段 1 · V1 工程化 Tool Agent 完成（约整体 20%）。已从"写死 Calculator 的 Agent"升级为"可扩展 Agent 框架"。

**下一步（阶段 2 · V2 多轮会话 + SQLite 记忆）**，要解决当前最明显的问题：
- **Agent 是"失忆"的**：每次 `run_agent` 只从 `[system, 当前user]` 开始，没有历史上下文。
- 要做：① 多轮会话（同一场对话记住历史）② SQLite 持久化（重启不丢）。

**后续阶段**：阶段 3 LangGraph+Checkpoint+SSE → 阶段 4 RAG+Chroma → 阶段 5 Task/Planner+APScheduler+HITL → 阶段 6 生产化（Docker+structlog）+ 缓冲。

---

*本总结基于 `01.docx` 的 V0→V1 前期记录与 2026-09-01 阶段 1 收尾的完整开发过程编写。*
