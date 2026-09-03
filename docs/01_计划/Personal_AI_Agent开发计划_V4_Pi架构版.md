# Personal AI Agent 开发计划（V4 · Pi 架构版）

> 版本：V4.0　编制日期：2026-09-02
> 依据：对 GitHub 开源项目 [badlogic/pi-mono](https://github.com/badlogic/pi-mono)（Pi Agent）的真实架构调研，重设计原《项目计划（重构版）V3》
> 主线：把"个人 AI 助手"从 V3 的功能拼盘，升级为 **Pi 风格**的"极简核心 + 深度可扩展"生产级 Agent 框架

---

## 〇、为什么重新设计（V3 → V4）

原 V3 计划按"技术名词堆阶段"组织（LangGraph / RAG / Task / Docker…），偏"会用某个库"。
调研 Pi Agent 后，它的工程哲学带来三点根本转变：

1. **从"堆工具"到"原子工具 + 组合"**：Pi 只封装 read/write/edit/bash 4 个原子工具，场景能力由 LLM 自主组合。工具系统不该无限膨胀。
2. **从"写功能"到"留钩子"**：Pi 的 25+ Hooks 分层分布在 Session / AgentLoop / Tool / Provider 上，可观测性、HITL、扩展全部建立在"钩子"之上，而不是各自为政。
3. **从"线性历史"到"树形会话"**：append-only JSONL + parentId 支持 Fork / 回滚 / 时间旅行，是长对话和复杂任务的基础。

V4 保留已完成进度（阶段 0-3），把后续阶段按这三点重构。

---

## 一、已完成进度（沿用 V3，不重做）

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段 0 | V0 最小 Agent（FastAPI + LLM 闭环） | ✅ 完成 |
| 阶段 1 | V1 工程化 Tool Agent（BaseTool + 注册表 + 有界循环） | ✅ 完成（Git 818daaf） |
| 阶段 2 | V2 多轮对话 + SQLite 记忆 | ✅ 完成（Git 3da7add） |
| 阶段 3 | V3 LangGraph + Checkpoint + SSE 流式 + HITL | 🔄 进行中（最小图+Checkpoint+API 已完成，SSE/HITL 待续） |

> 阶段 3 收尾后，LangGraph 图将作为 V4 各阶段能力的"承载底盘"。

---

## 二、重新设计的实现流程（V4：阶段 4-9 + 缓冲）

### 阶段 4｜V4 原子工具化 + 工具执行管道（约 1.5 周）

**核心目标**：向 Pi 学习——把"工具集合"升级为"4 个原子工具 + 可拦截的三阶段执行管道"。

**本周任务**：
1. 原子工具提炼：`read_file` / `write_file` / `edit_file` / `run_command` 四件套（替代或补充现有 calculator/time，保留 calculator 作为纯函数工具示范）
2. 三阶段流水线 `ToolPipeline`：
   - **Prepare**：解析工具名、参数规范化、JSON Schema 校验、`before_tool_call` 钩子
   - **Execute**：调用 `tool.run`，支持串行 / 并行模式
   - **Finalize**：`after_tool_call` 钩子，可覆盖结果 / 记录审计日志
3. 场景化能力验证：Agent 用 4 个原子工具**组合**完成"读文件 → 修改 → 执行"任务（不再新增场景工具）
4. 与 LangGraph 集成：ToolNode 改走 ToolPipeline

**里程碑**：Agent 靠 4 个原子工具完成一个"读取并修改项目文件"的真实任务；工具调用被钩子完整记录。
**核心理解**：为什么原子工具优于场景化工具；三阶段管道为何是 HITL / 审计的天然落点。

### 阶段 5｜V5 事件总线 + Hooks 扩展系统（约 1.5 周）

**核心目标**：建立 Pi 式"一个核心循环 + 无数钩子"的扩展架构。

**本周任务**：
1. 事件总线 `EventBus`：session_start / agent_start / message_update / tool_call / tool_result 等核心事件
2. 分层 Hooks（四层）：Session 层 / AgentLoop 层 / Tool 层 / Provider 层
3. 两阶段扩展加载：**加载阶段**（发现扩展、只注册）→ **绑定阶段**（注入运行时，才能调 sendMessage/setModel）
4. 落地两个真实扩展：
   - **observability 扩展**：所有事件打结构化日志（Pi 的 message_update / tool_result 埋点）
   - **approval 扩展**：`tool_call` 钩子拦截危险命令，人工审批后才执行（HITL，为阶段 9 铺垫）
5. 与 LangGraph 的 `interrupt` 对照：钩子 vs 图中断两种 HITL 实现

**里程碑**：写一个自定义扩展并热加载；危险操作被审批钩子拦截。
**核心理解**：事件驱动与可观测性的关系；为什么"加载/绑定"分离能保证安全隔离。

### 阶段 6｜V6 树形会话 + 上下文压缩（约 1.5 周）

**核心目标**：把"线性 SQLite 历史"升级为 Pi 式"append-only 树形 JSONL + Compaction"。

**本周任务**：
1. 树形存储：每条 Entry 带 `id / parentId / timestamp / type / content`，追加写入
2. 分支能力：从任意节点 Fork 出新会话（`parentId` 指向），支持时间旅行
3. 上下文重建：`build_session_context()` 从叶子回溯到根，重建消息链
4. Compaction 上下文压缩：
   - 三种触发：手动 `/compact`、Token 阈值、溢出恢复自动重试
   - 切割点规则：**工具结果不可切割**（工具调用与结果必须配对保留）
   - 跨回合切割：超预算时对助手消息切半，历史摘要 + 轮次前缀摘要合并
5. 与现有 SQLite / LangGraph Checkpoint 的关系：树形 JSONL 作为**会话主存储**，Checkpoint 可保留做图状态

**里程碑**：长对话超过阈值自动压缩；能从历史任意节点 Fork 出新分支。
**核心理解**：为什么 append-only 比 update 更稳；上下文压缩的工程细节（切割点、跨回合）。

### 阶段 7｜V7 Provider 抽象层 + 多模型（约 1 周）

**核心目标**：把写死的 DeepSeek 调用升级为 Pi 式统一 LLM 抽象层。

**本周任务**：
1. `LLMProvider` 抽象接口（chat / stream / tool_call 统一）
2. Provider 适配：OpenAI、Anthropic、Google、DeepSeek 至少 2-3 个可切换实现
3. `ModelRegistry` + 模型配置：`.env` / 配置文件切换模型，不碰业务代码
4. 流式输出统一：所有 provider 的流式接口归一化（对接阶段 3 的 SSE）
5. Token 计数 / 用量统计

**里程碑**：改一行配置即可在多个模型间切换；SSE 流式在所有 provider 下工作。
**核心理解**：抽象边界的价值；为什么 Provider 层是生产级 Agent 的地基。

### 阶段 8｜V8 多 Agent 编排（约 1.5 周）

**核心目标**：实现 Pi 的三类多 Agent 协作模式。

**本周任务**：
1. **Agent Chain（顺序流水线）**：定义 steps，每步输出作为下一步 `$INPUT`，`$ORIGINAL` 保留原始意图
2. **Agent Team（调度器）**：主 Agent 通过 `dispatch_agent` 把任务委派给专项子 Agent（如 planner / builder / reviewer）
3. **Subagent（后台并行）**：无头子 Agent 跑后台任务，通过事件上报进度
4. 主线落地：**求职助手**（planner 分析 JD → builder 匹配技能生成建议 → reviewer 复核）

**里程碑**：一个 5 步以上任务被自动拆解并流水线完成；一个后台子 Agent 并行执行。
**核心理解**：顺序流水线 vs 调度器 vs 并行的取舍；`$INPUT/$ORIGINAL` 如何保持意图不丢。

### 阶段 9｜V9 生产化 + 评估（约 1 周）

**核心目标**：可运行、可测试、可监控、可部署的第一版。

**本周任务**：
1. 可观测性：事件流 + 结构化日志 + 指标（复用阶段 5 的 observability 扩展）
2. 评估：任务成功率、工具选择正确率、压缩后召回、延迟与成本指标
3. Docker 化：API / 会话存储 / 定时任务容器编排
4. 文档收尾：README、架构图、API 文档、面试材料
5. 端到端演示流程

**里程碑**：`docker compose up` 一键启动；带日志、评估数据与完整文档。

### 缓冲（约 1 周）

延期兜底、薄弱环节深挖、演示打磨。每周五滚动复盘重排。

---

## 三、V3 → V4 阶段对照

| V3 计划 | V4 计划（Pi 架构版） | 变化理由 |
|---|---|---|
| 阶段 3 LangGraph+Checkpoint+SSE | 阶段 3（沿用） | 作为底盘保留 |
| 阶段 4 RAG 个人知识库 | **阶段 4 原子工具+工具管道** | 先立工具骨架，再谈知识库 |
| 阶段 5 Task/Planner | **阶段 5 事件总线+钩子** | 事件/钩子是自主执行的地基，前置 |
| 阶段 6 生产化 | **阶段 6 树形会话+压缩** | 长对话能力前置于生产化 |
| （原计划无） | **阶段 7 Provider 抽象** | 多模型是现实需求，提前 |
| （原计划无） | **阶段 8 多 Agent 编排** | 吸收 Pi 的 subagent/team/chain |
| （原计划无） | **阶段 9 生产化+评估** | 压缩后统一收口 |

> RAG（原阶段 4）未删除，调整为阶段 4 完成后的**支线**：先有原子工具（read 可读文档）再做知识检索，插入缓冲周或阶段 8 之后，避免工具与知识库两条线交叉。

---

## 四、技术栈选型（对照 Pi）

| 能力 | Pi 的实现 | 本项目的选择 |
|---|---|---|
| 原子工具 | read/write/edit/bash | read_file/write_file/edit_file/run_command（Python 实现） |
| 执行管道 | ToolExecutor 三阶段 | 自建 ToolPipeline + 钩子 |
| 事件/钩子 | 25+ Hooks | 自建 EventBus + 分层 Hook 注册 |
| 会话 | 树形 JSONL | SQLite 存储 JSONL 结构（零新依赖） |
| 压缩 | Compaction | 自建 compaction 模块 |
| Provider | 20+ 提供商 | LLMProvider 抽象 + 2-3 个适配器 |
| 编排 | subagent/team/chain | 自建（复用 LangGraph 子图） |

---

## 五、验收标准（逐阶段）

- **阶段 4**：Agent 用 4 个原子工具组合完成任务；工具管道带 before/after 钩子；审计日志完整。
- **阶段 5**：自定义扩展可加载绑定；危险操作被审批钩子拦截；事件日志结构化可查。
- **阶段 6**：会话可 Fork/回滚/时间旅行；超过阈值自动压缩；压缩后不丢工具配对。
- **阶段 7**：一行配置切换模型；流式在全部 provider 下工作。
- **阶段 8**：一个 5 步任务流水线完成；一个后台子 Agent 并行执行；求职助手可演示。
- **阶段 9**：docker compose 启动；具备日志/评估/文档；端到端演示。

## 六、主线场景贯穿（V4）

- 阶段 3：可控、可恢复、可流式的助手（已完成最小图）
- 阶段 4：会"动手干活"的助手（原子工具组合改文件、跑命令）
- 阶段 5：可观测、可审批的助手（谁调了什么、危险操作先批准）
- 阶段 6：长对话不"失忆"、可回溯的助手（树形 + 压缩）
- 阶段 7：多模型随便切的助手
- 阶段 8：会分工协作的助手（求职助手流水线）
- 阶段 9：可部署、可讲解、可演示的完整作品

---

*参考：pi-mono 架构调研（packages/agent、packages/ai、packages/coding-agent 源码 + 社区深度解析），2026-09-02。*
