# 面试题库 · 个人 Agent 方向（附回答要点）

> 基于 2026-09-01 ~ 09-03 学习内容整理，覆盖流式传输、LangGraph、工具调用、管道/钩子/事件、架构与工程实践。
> 难度标注：🔵 基础 ｜ 🟡 进阶 ｜ 🔴 深入
> 先遮住"回答要点"自测，再对照。

---

## 一、流式输出与网络

### 1. SSE 和 WebSocket 有什么区别？为什么 LLM 流式输出用 SSE？（🔵）

**回答要点**：
- SSE：基于 HTTP 的服务端**单向**推送，客户端自动重连，Text/Event-Stream 格式。
- WebSocket：**双向全双工**的独立协议，客户端可随时发消息回服务端。
- 关键区别在**方向性**：SSE 单向、WebSocket 双向。
- LLM 流式场景输出是"服务端 → 客户端"单向逐 token，不需要回传通道，SSE 更简单、天然适配；WebSocket 适合需要双向实时交互的场景（聊天室、游戏）。

### 2. SSE 在服务端怎么实现？有哪些工程细节？（🟡）

**回答要点**：
- FastAPI 用 `StreamingResponse` + `media_type="text/event-stream"`。
- 事件格式：`data: <json>\n\n`，可带 `event:` / `id:` 字段。
- 可能遇到的点：心跳/保活（注释行 `: ping`）、连接中断重连（Last-Event-ID）、中文编码、客户端 `EventSource` 只能 GET。
- 本项目：LangChain `ChatOpenAI(streaming=True)` + `stream_mode="messages"`，逐块取 `msg_chunk.content`。

### 3. 什么是全双工？什么场景该用 WebSocket？（🔵）

**回答要点**：全双工 = 双方可同时收发。适合服务端要主动推送、且客户端也要随时交互的场景（如实时协作、在线游戏）。若只是"服务端给客户端推流"，SSE 更轻。

---

## 二、LangGraph / Agent 编排

### 4. LangGraph 的 State / Node / Edge 分别是什么？reducer（add_messages）有什么用？（🟡）

**回答要点**：
- State：图的共享状态，用 `TypedDict` 定义结构；`Annotated[list, add_messages]` 声明该字段的**归约方式**。
- Node：一个处理步骤，普通函数 `(state) -> 部分更新 dict`。
- Edge：节点间的走向；普通边固定，条件边按 state 动态路由。
- `add_messages` reducer 解决"多个节点都往 messages 追加"的合并问题——不加 reducer，后写会覆盖前写；加了则自动 append 并按消息 id 去重。

### 5. "有工具调用就继续、没有就结束"这个条件边怎么写？（🔵）

**回答要点**：
- 写一个纯函数 `should_continue(state)`，读最后一条消息，`isinstance(last, AIMessage) and last.tool_calls` 为真返回 `"tools"`，否则返回 `END`。
- `add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})`。
- 好处：路由逻辑变成可单测的纯函数。

### 6. Checkpoint（SqliteSaver）解决什么问题？有什么坑？（🟡）

**回答要点**：
- 按 `thread_id` 持久化整图状态，服务重启后会话历史可恢复；不同 thread_id 隔离。
- 相比阶段 2 手写"只存 user/assistant"，语义更完整（连工具中间过程都保存）。
- 坑：`SqliteSaver.from_conn_string()` 返回的是**上下文管理器**，必须 `with ... as cp:` 进入才拿到 saver 实例，直接 compile 会报 Invalid checkpointer。

### 7. 用 interrupt 实现 HITL 人工审批，原理是什么？（🔴）

**回答要点**：
- `interrupt(value)` 只能在**图节点内部**调用，作用是"暂停图、把 value 抛给外部"。
- `invoke` 遇中断**不抛异常**，返回结果里带 `__interrupt__` 键（含 value）。
- 外部拿到 payload → 展示给人 → 用 `Command(resume=决策)` + **同一 thread_id** 恢复；恢复后节点从头重跑，interrupt 第二次返回 resume 值。
- 两段式驱动：`while "__interrupt__" in result:` 循环，因为一次调用可能多次中断。
- 设计原则：**外部只喂决策不越权**——approve/reject 都恢复图，由节点内部处理（拒绝也生成"被拒"ToolMessage 让 LLM 看到）。

### 8. 为什么说"图结构与节点实现解耦"？价值是什么？（🟡）

**回答要点**：
- 基础版 / HITL / SSE 三个版本的图**拓扑完全相同**（start→agent→条件→tools/end），差异只在节点内部实现（tools 节点加 interrupt、agent 节点换 ChatOpenAI）。
- 价值：加能力（审批、流式、日志）不用改拓扑，只改节点内部 → 图是稳定的"承载底盘"，后续阶段的能力都往节点里塞。

---

## 三、工具调用

### 9. Function Calling 的本质是什么？（🔵）

**回答要点**：LLM **不会执行函数**。它只根据工具 schema 决定"该调哪个、传什么参数"，返回**调用意图 JSON**（tool_calls）；真正执行函数的是应用代码。所以"工具"对 LLM 而言只是一份描述（name/description/parameters）。

### 10. tool_calls 的数据结构？AIMessage 和 ToolMessage 怎么配对？（🟡）

**回答要点**：
- `tool_calls` 是 **list**（一次可请求多个工具），每项 `{name, args(dict), id, type}`。
- AIMessage 发出 tool_calls（带 id）→ 执行工具 → 生成 ToolMessage，靠 **`tool_call_id`** 与对应调用配对回填给 LLM。
- 多工具调用：遍历 `last.tool_calls` 逐个执行，各自生成 ToolMessage。

### 11. 原子工具 vs 场景化工具？为什么原子工具更好？（🟡）

**回答要点**：
- 场景化：为每个需求写专用工具（calculator、get_time）→ 工具数量随需求线性膨胀。
- 原子化：read_file / write_file / edit_file / run_command 4 个覆盖"所有文件/命令操作"→ 数量固定，复杂行为由 LLM 自主组合。
- 本质是"能力供给" vs "需求点杀"：给 Agent 一套原子能力，让它自己编排，可扩展性更好。

### 12. 工具 schema 从哪来？为什么参数是"动态"的？（🟡）

**回答要点**：
- 每个工具类（继承 BaseTool）声明 `name/description/parameters`，注册进 `TOOL_SCHEMA`（就是给 LLM 看的 JSON Schema）。
- 参数值不是写死在代码里，而是 LLM 根据用户问题和 schema 现场生成 → 节点里只按 `tc["args"]` 透传执行。

---

## 四、管道 / 钩子 / 事件

### 13. 三阶段管道（Prepare/Execute/Finalize）为什么拆成三段？（🟡）

**回答要点**：
- 把"调工具"这件事的执行策略（校验、审批、审计、耗时）从工具内部**抽出来**统一处理。
- 三段各司其职：Prepare 决定"能不能执行"、Execute 真正执行、Finalize 处理"执行完之后"（记录/覆盖）。
- 好处：工具类只描述能力，执行策略集中；加策略不改工具、不加策略不动管道。

### 14. before_hook 如何实现"拦截"？返回值的语义？（🟡）

**回答要点**：
- 约定 `before_hook(name, args) -> str | None`：返回 `None` = 放行；返回**字符串** = 拦截，该字符串作为"拒绝原因"返回。
- 实现要点：**接住返回值 → `if denied:` 判断 → 提前 return**（不继续 Execute / after_hook）。常见 bug 是"调用了 hook 但没接返回值"，拦截形同虚设。

### 15. 钩子（Hook）和事件总线（EventBus）有什么区别？（🔴）

**回答要点**：
- 钩子：在**固定代码位置**调用你注册的函数（before/after 两个点），调用点写死。
- 事件总线：发布-订阅模型，`emit(type, data)` 在**任意时机/任意模块**广播，`subscribe(type, handler)` 在任意处监听；发布方不知道谁在听，监听方不知道谁产生，通过事件类型解耦。
- 一个事件可被**多个监听者**处理；事件是钩子的"系统化、解耦化"升级。本项目 ToolPipeline 的 before/after 钩子可视为事件总线的雏形。

### 16. 为什么"审批/日志/校验"这类横切关注点要挂管道/事件，而不是写进每个工具？（🟡）

**回答要点**：写进工具 → 每个工具重复代码、改需求要改所有工具、关注点与业务能力耦合。挂在管道/事件 → 集中实现、可插拔（想加审批就挂一个钩子/监听器，不想用就摘掉），符合"开闭原则"（对扩展开放、对修改关闭）。

---

## 五、架构与工程

### 17. Pi Agent 的架构核心是什么？为什么"树形会话 + 切割点压缩"重要？（🔴）

**回答要点**：
- 核心：极简核心 + 深度可扩展。事件流驱动（可观测性）+ 4 个原子工具（能力供给）+ 25+ 分层 Hooks（扩展点）+ 树形会话 + 多 provider。
- 树形会话：append-only JSONL + parentId，支持 Fork/回滚/时间旅行——长对话、复杂任务可回溯。
- 切割点压缩：**工具结果不可切割**（工具调用与结果必须配对保留），保证压缩后上下文语义完整；超预算时按轮次切半 + 历史摘要合并。

### 18. Codex 的"审批链"和"token 防线"是怎么设计的？（🟡）

**回答要点**：
- 三级审批链：Hook（自动检查）→ Guardian（策略门）→ User（最终人工），危险操作逐级升级审批。
- 三层 token 防线：全窗口硬上限（防 OOM）→ 压缩阈值（到线自动压缩）→ 剩余量提醒（提前预警）。
- 两级压缩：模型摘要压缩 / 按 token 预算直接换窗。

### 19. `python 脚本.py` 和 `python -m 包.模块` 的模块搜索差异？（🔵）

**回答要点**：`sys.path[0]`（首个搜索路径）不同——前者是**脚本所在目录**，后者是**当前工作目录**。项目里 `from tools.xxx` / `from app.xxx` 必须从根目录用 `-m`（或 uvicorn）跑，否则报 `No module named 'tools'/'app'`。

### 20. 循环导入是怎么产生的？怎么避免？（🟡）

**回答要点**：
- 产生：A import B，B 又 import A（或间接），Python 拿到**半初始化**模块 → `partially initialized module` / ImportError。
- 本项目例子：`run_command.py` 里 import 了 registry，而 registry 又 import run_command。
- 避免：保持**单向依赖**——工具类只依赖基类，注册统一由注册表负责；模块间依赖画成 DAG，不成环。

### 21. pydantic v2 为什么"子类覆盖基类字段必须带类型注解"？（🟡）

**回答要点**：pydantic 用类型注解做字段元数据推断。基类 `name: str = ""` 是注解字段，子类若写 `name = "x"`（无注解）会被当作"用非注解属性覆盖注解字段" → `PydanticUserError`。正确：覆盖时保持 `name: str = "x"` 带注解。

### 22. Windows 下读写文件为什么乱码？怎么防？（🔵）

**回答要点**：`open()` 默认用系统 locale 编码（Windows 常为 GBK/cp936），写中文进 UTF-8 预期的地方就乱。读写文件一律显式 `encoding="utf-8"`，跨平台行为一致。

### 23. 为什么顶层副作用代码要用 `if __name__ == "__main__":` 守卫？（🟡）

**回答要点**：模块被 import 时**会执行顶层代码**。联调/脚本逻辑若写在顶层，被 pytest 收集或别的模块 import 时就会误执行（本项目 `test_api.py` 顶层发 HTTP 请求导致 pytest 收集报 ConnectError）。守卫保证"只有直接运行该文件时才执行"，被 import 时只定义不执行。

---

## 六、综合（开放题）

### 24. 手写 Agent Loop 和 LangGraph 状态图的主要区别？为什么迁移？（🟡）

**回答要点**：
- 手写 Loop：状态（messages）和控制流（if/else）缠在一起，加能力要改主循环，难测试、难恢复。
- LangGraph：State 描述数据、Edge 描述决策，加能力只加节点/边；Checkpoint 免费持久化；条件边是可单测的纯函数。
- 迁移动机：可控、可恢复、可扩展（本项目"承载底盘"思路）。

### 25. 你做一个 AI Agent 项目，从 0 到 1 会怎么设计它的可扩展性？（🔴）

**回答要点**（开放，讲思路）：
- 工具层：原子工具 + 注册表（schema 驱动，加工具=写类+注册一行）。
- 执行层：管道三段（校验/审批/审计横切），钩子可插拔。
- 事件层：EventBus 发布-订阅，可观测性（谁调了什么、耗时、结果）基于事件埋点。
- 编排层：图/状态机承载，Checkpoint 持久化。
- 会话层：树形/可回溯 + 上下文压缩（工具配对不可切）。
- 模型层：Provider 抽象，一行切模型。
- 本质：**每层留扩展点**（钩子/事件/注册表），而不是把能力写死。
