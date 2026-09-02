# Personal AI Agent 开发总结 · 阶段 3

**阶段名称**：V3 LangGraph 重构 + Checkpoint（最小图部分）
**完成时间**：2026-09-02（阶段 3 启动日，按计划"先跑通最小图再扩展"）

---

## 一、阶段目标

把手写 Agent Loop 迁移为 **LangGraph 状态图**，获得：可控、可恢复、可扩展的编排能力。
按计划风险预案（LangGraph 学习曲线陡，先跑通最小图再扩展），今天完成最小图 + Checkpoint 持久化。

## 二、完成情况

| 能力 | 验收结果 |
|---|---|
| LangGraph 图编排（StateGraph + AgentNode + ToolNode + 条件边） | ✅ agent → tools → agent → END 完整跑通 |
| AgentState 设计（add_messages reducer） | ✅ 历史消息自动累积合并 |
| 复用既有工具（calculator / time） | ✅ 加工具仍"写类 + 注册一行"，零改动 |
| Checkpoint 会话持久化（SqliteSaver） | ✅ 重启后同一 session_id 恢复记忆；不同 session 隔离 |
| FastAPI 集成 `/api/chat_graph` | ✅ 工具调用（3×7=21）与多轮续问（21+3=24）均正常 |
| 自动化测试 | ✅ 26 passed（原 20 + 新增 6） |

## 三、代码改动清单

| 文件 | 状态 | 作用 |
|---|---|---|
| `app/graph_state.py` | 🆕 新建 | AgentState：`messages: Annotated[list, add_messages]` + `session_id` |
| `app/graph_agent.py` | 🆕 新建 | LangGraph 图：agent 节点 / tools 节点 / 条件边 / build_graph / run_graph_agent / get_checkpointer |
| `app/main.py` | ✏️ 修改 | 新增 `/api/chat_graph` 端点（保留原 `/api/chat`）；版本号 0.1.0 → 0.2.0 |
| `test/test_graph_agent.py` | 🆕 新建 | 6 个测试：直接回答 / 工具链 / 条件边 / 工具节点 / checkpoint 持久化 / 会话隔离 |
| `docs/阶段3_手写Loop痛点与LangGraph迁移.md` | 🆕 新建 | 迁移动机 + 迁移实践记录 |
| `demo/demo_graph_agent.py` | 🆕 新建 | 真实 LLM 联调脚本（问答/计算器/时间） |
| `demo/demo_graph_checkpoint.py` | 🆕 新建 | Checkpoint 多轮记忆联调脚本 |
| `requirements.txt` | ✏️ 修改 | +langgraph==1.2.11、langgraph-checkpoint-sqlite==3.1.1 |
| `.gitignore` | ✏️ 修改 | +*.sqlite、*.sqlite3（排除 checkpoint 文件） |

## 四、架构（LangGraph 版）

```
用户消息 → invoke(config={"thread_id": session_id})
              │
              ▼
           START ──> agent（LLM 决策）
                          │
                有 tool_calls ?
                   ├─ 否 ─> END（返回答案）
                   └─ 是 ─> tools（execute_tool 复用）──> agent（回到决策）
```

关键设计：
1. **消息格式只在 LLM 边界转换**：`to_openai_messages()` 处理 LangChain AIMessage.tool_calls ↔ OpenAI tool_calls 的结构差异。
2. **工具节点复用 `execute_tool`**：不引入 LangChain Tool 包装，保住"注册即用"的开闭原则。
3. **system prompt 只注入一次**：有 Checkpoint 用 `get_state` 判断 thread 是否已有历史。
4. **session_id ↔ thread_id**：客户端 `session_id` 映射 LangGraph `thread_id`，与阶段 2 会话模型一致。

## 五、核心收获

1. **状态 vs 控制流分离**：手写 Loop 里状态（messages）和控制流（if/else）缠在一起；LangGraph 用 State 描述数据、用 Edge 描述决策，扩展能力只加节点/边。
2. **add_messages reducer**：自动 append + 按 id 合并 AIMessage，历史维护从"手写 append"变成"声明式"。
3. **Checkpoint = 免费持久化**：`SqliteSaver` 自动保存整图状态，按 thread_id 恢复，替换了阶段 2 手写的"只存 user/assistant 两条"方案，语义更完整（连 tool 过程都在）。
4. **条件边是核心抽象**：`should_continue` 返回 "tools" 或 END，把"是否继续调工具"这个决策变成一个可测试的纯函数。
5. **mock 测试验证图路由**：patch `call_llm` 让图离线跑通，测试在真实 API 之前验证了工具链与持久化。

## 六、下一步（阶段 3 后续，按计划推进）

- SSE 流式输出（逐 token 推送给前端）
- HITL：`interrupt` 实现工具执行前人工审批（为阶段 5 铺垫）
- LangGraph 调试：图结构可视化（mermaid / get_graph）、状态回溯
- `/api/chat` 旧端点切换到 LangGraph 版（当前保留双端点便于对照）
