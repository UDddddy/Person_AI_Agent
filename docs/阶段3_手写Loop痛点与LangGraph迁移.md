# 阶段 3 · 手写 Agent Loop 痛点与 LangGraph 迁移记录

> 日期：2026-09-02
> 对应计划：《Personal AI Agent 项目计划（重构版）》阶段 3｜V3 LangGraph 重构与 Checkpoint

## 一、手写 Agent Loop 的痛点（迁移动机）

V1/V2 的 `app/agent.py::run_agent` 是手写 `for i in range(max_iterations)` 循环，
长这样：

```
for i in range(max_iterations):
    response = chat_with_tools(messages)
    if not response.tool_calls: return answer
    messages.append(assistant tool_call)
    for tc in response.tool_calls:
        messages.append(tool result)
```

它暴露了四类问题：

| 痛点 | 具体表现 | 后果 |
|---|---|---|
| ① 状态散落在局部变量 | `messages` 是函数里的一个 list，靠手工 `append` 维护 | 历史一旦漏 append 就"失忆"；无法查看"当前走到哪一步" |
| ② 控制流写死 | 循环、分支全靠 `if/else` 硬编码 | 想加"工具执行前人工审批"、"中断后恢复"几乎要重写 |
| ③ 会话恢复要自研 | 靠 `db.py` 手工存 user/assistant 两条 | 只存了最终答案，丢失中间 tool 过程；语义不完全 |
| ④ 无流式、无中断 | 同步返回整段答案 | 无法逐 token 输出；无法在工具执行前暂停征求同意 |

**一句话**：手写 Loop 是"过程式"的——状态、控制流、持久化、恢复全部自己造轮子，
扩展一个能力（流式 / 审批 / 断点）就要动主干逻辑。

## 二、LangGraph 如何解决

LangGraph 用 **图** 来表达 Agent：状态（State）、节点（Node）、边（Edge）分离。

| 手写 Loop | LangGraph |
|---|---|
| `messages` 局部变量 + 手工 append | `AgentState.messages` 用 `add_messages` reducer 自动累积合并 |
| `if not tool_calls: return` | 条件边 `should_continue`：有 tool_calls → tools，否则 → END |
| 自研 SQLite 存两条 | Checkpoint（`SqliteSaver`）按 `thread_id` 自动保存全部状态，可中断/恢复 |
| 改控制流要重写 | 加节点、加边、加 Checkpoint 即可，主干不动 |

## 三、本次迁移的架构

```
用户消息 ──> invoke(config={"thread_id": session_id})
                │
                ▼
             START
                │
                ▼
        ┌─── agent（LLM 决策）───────────┐
        │     返回 AIMessage              │
        │     有 tool_calls?              │
        │       ├─ 否 ──> END（返回答案） │
        │       └─ 是 ──> tools（执行工具）│
        │                    │  ToolMessage
        └────────────────────┘
```

- **AgentState**（`app/graph_state.py`）：`messages: Annotated[list, add_messages]` + `session_id`
- **agent 节点**：`call_llm` 把 LangChain 消息转成 OpenAI 格式调用 DeepSeek，返回 AIMessage
- **tools 节点**：复用既有 `execute_tool`（BaseTool 统一异常兜底），返回 ToolMessage
- **条件边**：`should_continue` 判断最后一条 AIMessage 是否带 tool_calls
- **Checkpoint**：`SqliteSaver.from_conn_string("app/checkpoints.sqlite")`，thread_id = session_id

## 四、关键设计决策

1. **LangChain messages 做图内消息格式，OpenAI 格式只在 LLM 边界转换**：
   `to_openai_messages()` 统一转换 AIMessage.tool_calls 与 OpenAI 的 tool_calls 结构差异。
2. **复用既有工具体系**：tools 节点调 `execute_tool(name, args)`，不引入 LangChain Tool 包装，
   保持"加工具 = 写类 + 注册一行"的开闭原则不变。
3. **system prompt 只注入一次**：有 Checkpoint 时用 `get_state` 判断该 thread 是否已有历史，
   避免重复注入；无 Checkpoint 时每次注入（全新会话）。
4. **session_id ↔ thread_id**：客户端仍传 `session_id`，LangGraph 侧映射为 `thread_id`，
   与阶段 2 的会话模型语义一致。

## 五、验证结果

| 验证项 | 结果 |
|---|---|
| 无工具问答 | ✅ 图返回直接回答 |
| calculator 工具调用（1+1=2） | ✅ agent → tools → agent 完整链路 |
| get_current_time 工具调用 | ✅ 返回当前时间 |
| Checkpoint 多轮记忆（重启后想起上轮约定） | ✅ |
| Checkpoint 会话隔离（不同 thread 互不干扰） | ✅ |
| 自动化测试 | ✅ 26 passed（原 20 + 新增 6） |

## 六、下一步（阶段 3 后续）

- SSE 流式输出（逐 token）
- HITL：`interrupt` 实现工具执行前人工审批（为阶段 5 铺垫）
- FastAPI `/api/chat` 切换到 LangGraph 版
- 图结构可视化与状态回溯
