# 阶段 1 学习文档：Tool Agent 工程化

> 适用阶段：阶段 1｜V1 工程化 Tool Agent（约 1 周）
> 技术栈：Function Calling（工具调用）、抽象基类、注册表模式、异常处理、AST 安全解析
> 配套项目代码：
>
> `tools/base.py`
>
> 、
>
> `tools/registry.py`
>
> 、
>
> `tools/executor.py`
>
> 、
>
> `tools/calculator.py`
>
> 、
>
> `tools/time_tool.py`
>
> 、
>
> `app/llm.py`



***

## 0. 这份文档怎么用

阶段 1 是你项目**脱胎换骨**的一步：V0 只能 "动嘴"（聊天），V1 能 "动手"（调用工具干活）。这份文档要让你彻底搞懂一个关键问题：

> **大模型是怎么 "调用" 你的 Python 函数的？它真的会执行代码吗？**

读完你要能画出 Function Calling 的完整数据流，并解释你写的每个文件为什么存在。



***

## 1. 核心概念：Function Calling（工具调用）—— 必须吃透

### 1.1 一个反直觉的事实

**大模型根本不会执行你的函数。** 它做的是：

> 你告诉它 "有哪些工具可用"，它看完你的问题后，
>
> **生成一段 JSON，说 " 我想调用&#x20;**
>
> `calculator`
>
> **，参数是&#x20;**
>
> `{"expression": "1+1"}`
>
> **"**
>
> 。至于这段 JSON 怎么变成真的计算结果 —— 是你自己执行的。

这就是 Function Calling（函数调用 / 工具调用）的本质：**LLM 负责 "决定"，你负责 "执行"**。

### 1.2 完整的两段式协议



```
第一段：你告诉 LLM 有哪些工具

&#x20; POST /chat/completions

&#x20; messages: \[...]

&#x20; tools: \[ { "type": "function", "function": { "name": "calculator", "description": "...", "parameters": {...} } } ]

&#x20;               ↑ 这就是你 tools/schemas.py / BaseTool.schema() 生成的

第二段：LLM 决定要调工具，返回的不是答案，而是"调用意图"

&#x20; response.choices\[0].message.tool\_calls = \[

&#x20;   { "id": "call\_xxx", "type": "function",

&#x20;     "function": { "name": "calculator", "arguments": '{"expression": "1+1"}' } }

&#x20; ]

&#x20;               ↑ arguments 是一个 JSON 字符串！

之后你执行工具，再把结果作为 "role":"tool" 消息放回 messages，重新问 LLM
```

**关键点**：



* `arguments` 是**字符串**（不是 dict），所以要 `json.loads` 解析 —— 这就是你 `BaseTool.execute()` 里那段 `json.loads(arguments)` 的原因。

* LLM 一次可以返回**多个** `tool_calls`（并行调用多个工具）—— 所以你 `run_agent` 里用 `for tool_call in response.tool_calls`。

* 工具结果要带上 `tool_call_id` 和 LLM 的 `tool_calls[].id` 对应，LLM 才知道 "这个结果是回答哪个调用的"。

### 1.3 完整一轮 "带工具" 的对话



```
user: 请计算 1+1

assistant: (tool\_calls=\[calculator, {"expression":"1+1"}])   ← LLM 不回答，而是声明要调工具

tool: (tool\_call\_id=call\_xxx, content="2")                    ← 你把真实结果放回去

assistant: 1+1 等于 2                                          ← LLM 看到结果，给最终答案
```



***

## 2. 为什么需要 "工程化"：抽象、注册、执行

阶段 1 的目标不是 "能用工具"，而是 \*\*"加一个新工具不用改主循环"\*\*。这靠三层设计：



```
tools/base.py      抽象层  → 定义"一个工具长什么样"（统一接口）

tools/registry.py  注册层  → 集中登记"有哪些工具"

tools/executor.py  执行层  → 根据名字找到工具并执行
```

**核心原则：开闭原则（OCP）**—— 对扩展开放，对修改关闭。加新工具 = 新建一个类 + 注册，**主逻辑一行不用改**。



***

## 3. BaseTool：抽象基类 —— 为什么把工具 "统一建模"

### 3.1 你的代码



```
class BaseTool(BaseModel):

&#x20;   name: str = ""

&#x20;   description: str = ""

&#x20;   parameters: dict = {}

&#x20;   def run(self, \*\*kwargs):          # 子类要实现：真正干活

&#x20;       raise NotImplementedError(...)

&#x20;   def execute(self, arguments):     # 基类实现：解析参数 + 错误处理

&#x20;       argument = json.loads(arguments) if isinstance(arguments, str) else arguments

&#x20;       try:

&#x20;           result = self.run(\*\*argument)

&#x20;           return str(result)

&#x20;       except ZeroDivisionError as e:

&#x20;           return ValueError(f"计算错误分母不能为零: {str(e)}")

&#x20;       except Exception as e:

&#x20;           return ValueError(f"计算错误: {str(e)}")

&#x20;   def schema(self):                 # 生成给 LLM 看的工具描述

&#x20;       return {"type": "function", "function": {...}}
```

### 3.2 为什么需要这个抽象

**所有工具都有的共同职责**：① 告诉 LLM"我是谁、要什么参数"（`schema`）② 把 LLM 给的字符串参数解析成能用的东西（`execute`）③ 真正干活（`run`）。

把这三件事统一成**一个基类**后：



* 新增工具只写 `run`，其余自动继承 ——**不用重复写 schema 和参数解析**。

* `BaseTool` 继承 `pydantic.BaseModel`：name/description 自动有类型校验，且注册时 `CalculatorTool()` 自动实例化。

### 3.3 execute 与 run 为什么分开（关键设计）



| 方法                   | 职责                     | 谁调用 | 抽象级别 |
| -------------------- | ---------------------- | --- | ---- |
| `run(**kwargs)`      | 纯粹的业务逻辑                | 执行器 | 子类实现 |
| `execute(arguments)` | 参数解析 + 异常兜底 + 统一返回 str | 执行器 | 基类统一 |

**好处**：子类写 `run` 时**不用关心参数是从哪来的、出错怎么办**，只管 "给我参数，我算出结果"。参数解析、异常处理这些 "横切关注点" 被基类统一收口 —— 这就是 "控制反转 / 模板方法" 思想的雏形。



***

## 4. Registry 注册表：为什么 "集中登记"



```
Tools = {}          # name → 工具实例

TOOL\_SCHEMA = \[]    # 所有工具的 schema 列表

def register\_tool(tool: BaseTool):

&#x20;   Tools\[tool.name] = tool

&#x20;   TOOL\_SCHEMA.append(tool.schema())

register\_tool(CalculatorTool())

register\_tool(GetCurrentTimeTool())
```

**注册表模式**解决两个问题：



1. **给 LLM 看什么**：`TOOL_SCHEMA` 直接传给 `chat_with_tools` 的 `tools=` 参数 ——**注册表是 "工具清单" 的唯一权威来源**。加新工具，LLM 自动 "知道" 它存在。

2. **执行时找谁**：`Tools[tool.name]` 按名字拿到实例 ——**注册表是 "名字 → 实例" 的映射**。

> **对照你的项目**
>
> ：对比 
>
> `tools/schemas.py`
>
> （你早期手写死的一个 
>
> `CALCULATOR_TOOL`
>
>  dict）和 
>
> `BaseTool.schema()`
>
> （自动生成）—— 这就是 "硬编码 vs 程序化" 的区别。注册表让 schema 永远和工具类定义
>
> **同步**
>
> ，不会改了一边忘了另一边。



***

## 5. Executor 执行器：最后一公里的兜底



```
def execute\_tool(tool\_name, arguments):

&#x20;   tool = Tools.get(tool\_name)          # 找不到 → None

&#x20;   if tool is None:

&#x20;       return f"找不到工具 {tool\_name}"  # 优雅降级，而不是抛异常崩溃

&#x20;   return tool.execute(arguments)
```

**为什么需要执行器这一层**：`run_agent` 不该关心 "工具怎么找、怎么解析、怎么调"，它只调用 `execute_tool(name, args)` 就完事。执行器是 \*\*"调用方" 和 "工具" 之间的适配层 \*\*——LLM 说错了工具名、传错了参数，都在这里被拦截、变成可读的错误信息，而不是让整个 Agent 崩溃。



***

## 6. 安全计算：为什么用 ast 而不是 eval（重要！）

### 6.1 你的 CalculatorTool 为什么不直接 `eval(expression)`



```
\# ❌ 危险：eval 会执行任意代码！

eval("\_\_import\_\_('os').system('rm -rf /')")   # 直接执行系统命令！

\# ✅ 安全：你用的 ast 白名单方案

tree = ast.parse(expression, mode="eval")

for node in ast.walk(tree):

&#x20;   if not isinstance(node, allow\_node):      # 只允许特定节点

&#x20;       return "表达式中存在不支持的节点"
```

### 6.2 底层原理：AST（抽象语法树）

Python 在 "真正执行代码" 之前，会先把源代码解析成一棵**语法树**（AST）。比如 `1+2*3` 会变成：



```
&#x20;       BinOp(+)

&#x20;       /     \\

&#x20; Constant(1)  BinOp(\*)

&#x20;             /       \\

&#x20;       Constant(2)   Constant(3)
```

`eval` 会把整棵树 "无差别执行"—— 所以 `os.system` 这种危险节点也会被执行。而 `ast` 方案是：**先把表达式解析成树，然后自己写一个 "树的遍历器"，只允许白名单里的节点**（`Constant`/`Add`/`Sub` 等数学运算），遇到白名单外的节点（比如 `Call`，也就是函数调用）直接拒绝。

**这就是 "白名单安全" 思想**：不靠 "禁止坏东西"（黑名单，总有漏网），而是 "只允许好东西"（白名单，天生安全）。这个思想做 LLM 应用尤其重要 ——**LLM 生成的输入永远不可信**，任何执行路径都要按 "不可信输入" 来防御。

> **对照你的项目**
>
> ：你的 
>
> `calculate_node`
>
>  就是一个递归 AST 求值器（处理 
>
> `Constant`
>
> /
>
> `BinOp`
>
> /
>
> `UnaryOp`
>
> ），
>
> `calculate`
>
>  负责解析 + 白名单校验 + 大小 / 类型防护。你还在代码里防了 bool 类型、超大数字、除零 —— 这就是 "对不可信输入做多层校验" 的工程素养。



***

## 7. 工具调用的完整数据流（阶段 1 全貌）



```
用户: "请计算 1+1"

&#x20; │

&#x20; ▼

run\_agent(messages)

&#x20; │  循环开始

&#x20; ▼

chat\_with\_tools(messages, tools=TOOL\_SCHEMA)   ← 注册表提供工具清单

&#x20; │

&#x20; ▼

LLM 返回: assistant(tool\_calls=\[calculator, '{"expression":"1+1"}'])

&#x20; │

&#x20; ├─ 没有 tool\_calls → 返回最终答案 ✅

&#x20; │

&#x20; ▼

执行: execute\_tool("calculator", '{"expression":"1+1"}')

&#x20; │    → Tools.get → tool.execute → json.loads → run(expression="1+1") → ast 安全计算 → "2"

&#x20; ▼

把结果放回 messages: {"role":"tool", "tool\_call\_id":..., "content":"2"}

&#x20; │

&#x20; ▼

回到循环，再次 chat\_with\_tools → LLM 看到结果 → "1+1 等于 2" → 无 tool\_calls → 返回
```

**你在每一层学到的东西**：



* `chat_with_tools`：怎么把工具清单传给 LLM、怎么读 `tool_calls`

* `BaseTool.schema`：怎么描述 "这个工具长什么样"（JSON Schema）

* `BaseTool.execute`：怎么解析不可信的 JSON 字符串、怎么兜底异常

* `registry`：怎么集中管理工具清单

* `executor`：怎么按名字路由执行

* `calculator`：怎么安全地执行用户给的表达式（ast 白名单）



***

## 8. 常见坑 & 自测

### 常见坑



1. `arguments` 是字符串不是 dict—— 忘了 `json.loads` 会直接 `TypeError`。

2. 工具结果忘了带 `tool_call_id` → LLM 无法把结果对应到调用，会 "失忆"。

3. `eval` 直接用 → 灾难性安全漏洞（LLM 可被诱导注入恶意代码）。

4. 注册表忘了 `register_tool` → LLM 永远不知道有这个工具，表现就是 "它死活不用计算器"。

5. `execute` 里 `return ValueError(...)` 而不是 `raise`—— 注意你的代码里混用了两者（return 一个异常对象其实不是好实践，建议统一 `return f"错误信息"`，这是可以改进的点）。

### 自测



1. 一句话说清：LLM 到底 "执行" 了你的函数吗？它返回了什么？

2. `arguments` 是什么类型？为什么 `execute` 要先 `json.loads`？

3. 加一个新工具 `GetWeatherTool`，列出你**必须改**的所有文件（验证开闭原则）。

4. 为什么 `eval("__import__('os').system('ls')")` 是危险的？`ast` 方案怎么防住它？

5. 如果 LLM 同时要调两个工具（`tool_calls` 有两个元素），你的代码怎么处理？

6. 指出你 `execute` 里 `return ValueError(...)` 的写法问题，并改成更合理的返回。



***

**下一步**：V1 能 "动手" 了，但每轮都是全新开始 ——V2 要让它 "记住" 之前的对话。见 `阶段2_多轮会话与SQLite记忆.md`。