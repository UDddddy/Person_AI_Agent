# Pi Agent 架构设计学习笔记

> 对象：
>
> [badlogic/pi-mono](https://github.com/badlogic/pi-mono)
>
> （Pi Agent）・TypeScript/Bun 实现
> 说明：本文基于对 
>
> `packages/agent`
>
> 、
>
> `packages/ai`
>
> 、
>
> `packages/coding-agent`
>
>  源码的精读整理，用于学习一个生产级 Agent 的完整设计。所有函数名、常量、阈值均来自真实源码。



***

## 0. 一句话定位

Pi 是一个 **"极简核心 + 深度可扩展"** 的终端编码 Agent：核心循环只有几十行，真正的复杂度全部沉淀在**事件流、钩子（Hooks）、树形会话、上下文压缩、Provider 抽象**五条扩展线上。它的工程哲学是 ——**用一堆小而正交的 "钩子点" 代替一堆写死的功能**。



***

## 1. 总体架构：10 个包的 monorepo



```
pi-mono/

├── packages/

│   ├── agent/             # 核心 Agent 循环（无 UI、无编码逻辑，纯循环）

│   │   └── src/

│   │       ├── agent-loop.ts    # ★ 核心执行循环

│   │       ├── agent.ts         # Agent 定义

│   │       ├── node.ts          # 会话树节点（re-export）

│   │       ├── proxy.ts         # 多 Agent 代理

│   │       ├── stream-fn.ts     # 流式函数抽象

│   │       ├── harness/         # 测试框架

│   │       └── search/          # 搜索

│   ├── ai/                # LLM Provider 抽象（★ 70+ provider）

│   │   └── src/

│   │       ├── providers/       # openai/anthropic/deepseek/google/…每个一个文件

│   │       ├── model-catalog.ts # 模型目录

│   │       ├── models-store.ts  # 模型存储

│   │       └── compat.ts        # 兼容层

│   ├── coding-agent/      # 编码 Agent 具体实现（会话、工具、压缩）

│   │   └── src/

│   │       ├── core/

│   │       │   ├── agent-session.ts     # 会话

│   │       │   ├── compaction/          # ★ 上下文压缩

│   │       │   │   ├── compaction.ts    #   核心压缩

│   │       │   │   └── branch-summarization.ts  # 分支摘要

│   │       │   ├── tools/               # ★ 工具系统

│   │       │   ├── event-bus.ts         # 事件总线

│   │       │   ├── session-manager.ts   # 树形会话管理

│   │       │   ├── model-registry.ts    # 模型注册

│   │       │   └── system-prompt.ts     # 系统提示词构建

│   │       └── ...

│   ├── client/  protocol/  server/  telemetry/

│   ├── session-backends/  # 会话持久化后端

│   ├── evals/             # 评估

│   └── tui/               # 终端 UI
```

**分层关系**：`agent`（通用循环，不依赖具体工具）→ `coding-agent`（把通用循环接到具体工具 / 会话 / 压缩上）→ `ai`（只管 LLM 调用）。上层依赖下层，下层不知道上层存在。



***

## 2. 核心执行循环 `agent-loop.ts`（★ 最重要）

### 2.1 双嵌套循环



```
// 外层：只要还有排队消息就继续整轮

while (true) {

&#x20; let hasMoreToolCalls = true;

&#x20; // 内层：处理工具调用 + 中途插入的消息

&#x20; while (hasMoreToolCalls || pendingMessages.length > 0) {

&#x20;   // 1. 若上轮完成，先跑 prepareNextTurn()（可做压缩/换模型）

&#x20;   // 2. 注入 pendingMessages（用户思考时输入的消息）

&#x20;   // 3. streamAssistantResponse()  → 得到 assistant 消息

&#x20;   // 4. 提取 toolCalls → executeToolCalls()

&#x20;   // 5. hasMoreToolCalls = 是否还有工具调用

&#x20; }

&#x20; // 内层退出 = Agent 认为该停了。检查是否有 follow-up 消息，有则继续外层

}
```



* **内层**：一轮内 "模型输出 → 工具执行 → 再输出" 的循环（Agent 决策环）。

* **外层**：处理 "用户排队消息"（`getSteeringMessages`）和 "用户后续消息"（`getFollowUpMessages`），让 Agent 在等待时也能响应新输入。

### 2.2 事件流（可观测性的地基）

整个循环是一个 `EventStream<AgentEvent, AgentMessage[]>`，事件类型固定且正交：



| 事件                                                                      | 时机                    |
| ----------------------------------------------------------------------- | --------------------- |
| `agent_start` / `agent_end`                                             | 整个 Agent 会话开始 / 结束    |
| `turn_start` / `turn_end`                                               | 单轮开始 / 结束             |
| `message_start` / `message_update` / `message_end`                      | 消息生命周期（流式增量走 update）  |
| `tool_execution_start` / `tool_execution_update` / `tool_execution_end` | 工具执行生命周期              |
| （内部）                                                                    | `tool_result_message` |

**设计要点**：所有 UI、日志、审计、HITL 都监听这套事件，而不是各自实现一遍。这也是 V4 计划 "事件总线 + Hooks" 的直接参照。

### 2.3 消息转换边界



```
// AgentMessage\[]（内部统一模型）只在 LLM 调用边界转成 Message\[]

const llmMessages = await config.convertToLlm(messages);
```



* 内部消息模型 `AgentMessage` 有更多角色：`user / assistant / toolResult / bashExecution / custom / branchSummary / compactionSummary`。

* `bashExecution`（命令 + 输出）和 `custom`（扩展自定义）是 pi 特有的角色，转换时才折叠成 LLM 能理解的 `toolResult` 等。

* 好处：内部逻辑不绑定任何 provider 的消息格式，方便扩展。

### 2.4 工具三阶段执行管道（★）



```
async function executeToolCalls(...) {

&#x20; // 顺序 or 并行：全局配置 toolExecution === "sequential"

&#x20; // 或该工具标注 executionMode === "sequential" 时强制串行

}

// 每个工具调用经历三阶段：

// 1. prepareToolCall

//    - prepareArguments（参数预处理）

//    - validateToolArguments（JSON Schema 校验）

//    - config.beforeToolCall?  → 钩子，可 return {block:true} 拦截（HITL 落点）

// 2. executePreparedToolCall

//    - tool.execute(id, args, signal, 部分结果回调)

//    - 部分结果通过 tool\_execution\_update 事件流式上报（长任务进度）

// 3. finalizeExecutedToolCall

//    - config.afterToolCall? → 钩子，可改写结果/附加 usage/设置 terminate
```

**关键细节**：



* 工具可用 `terminate` 标志：执行后让整个循环停止（如用户说 "够了"）。

* 工具参数校验失败 / 工具不存在 / 钩子拦截 → 直接生成错误 `toolResult` 消息返回给模型，**不抛异常中断循环**（错误也走消息通道，模型能自我修正）。

* 并行执行：所有工具 `Promise.all`，但结果按原顺序排列回写（`orderedFinalizedCalls`）。

### 2.5 截断保护：stopReason === "length"



```
if (message.stopReason === "length") {

&#x20; // 输出被 token 上限截断，工具调用参数可能不完整

&#x20; await failToolCallsFromTruncatedMessage(toolCalls, emit);

}
```

**精髓**：模型输出被 token 截断时，流式拼接出来的工具参数可能是 "能解析但残缺" 的。pi 的策略是**全部作废并报错**，让模型重新发 —— 而不是冒险执行不完整参数。这是一个很隐蔽但极其重要的正确性设计。

### 2.6 轮次间准备 `prepareNextTurn`



```
const nextTurnSnapshot = await config.prepareNextTurn?.(lastCompletedTurn);

if (nextTurnSnapshot) {

&#x20; currentContext = nextTurnSnapshot.context ?? currentContext;

&#x20; config.model = nextTurnSnapshot.model ?? config.model;  // 可换模型

&#x20; config.reasoning = nextTurnSnapshot.thinkingLevel;       // 可换思考级别

}
```



* 注释明确写着："Preparation can be long-running (for example, compaction)"。

* **pi 的压缩发生在轮次之间**（上一个 turn 结束后、下一个 turn 开始前），通过 `prepareNextTurn` 钩子触发。这是压缩时机的关键选择：不影响正在进行的 turn 的流式输出。



***

## 3. 树形会话存储（★ 与线性历史最大的不同）

### 3.1 设计



* 存储为 **append-only JSONL**（只追加，不修改历史）。

* 每条 `SessionEntry` 带 `id` + `parentId`，天然形成**树**：



```
entry(id=A)             ← 根

&#x20; ├─ entry(id=B, parent=A)  ← 分支 1

&#x20; │    └─ entry(id=D, parent=B)

&#x20; └─ entry(id=C, parent=A)  ← 分支 2（从 A 处 fork）

&#x20;      └─ entry(id=E, parent=C)
```



* `SessionEntry` 类型：`message` / `compaction` / `branch_summary` / `custom_message` / `thinking_level_change` / `model_change` / `label` / `session_info` 等。

* 当前 "位置" 用一个叶子 id 表示（如 `E`），从该叶子沿 parentId 回溯到根即得当前上下文链。

### 3.2 能力



* **Fork**：任何节点都可以作为新分支起点（把新条目 parentId 指向它）。

* **时间旅行 / 回溯**：把位置指到历史任意节点，即可 "回到过去" 再看。

* **上下文重建**：`buildSessionContext()` 从叶子回溯到根，重建消息链。

### 3.3 为什么 append-only 优于 update



* 不改写历史 → 天然可审计、可回滚、可并发写（无锁）。

* 压缩也是 "新增一条 compaction 条目" 而非覆盖旧记录 —— 旧历史完整保留，随时可查。



***

## 4. Token 计算（compaction.ts）

### 4.1 估算启发式 `estimateTokens`（保守高估）



```
export function estimateTokens(message: AgentMessage): number {

&#x20; // 字符数 / 4 ≈ token 数（对中英文都是较保守的估计）

&#x20; // user/toolResult:        content 字符 / 4

&#x20; // assistant:              text + thinking + 工具名 + JSON.stringify(args) 之和 / 4

&#x20; // bashExecution:          command.length + output.length 之和 / 4

&#x20; // branchSummary/compactionSummary: summary.length / 4

&#x20; // 图像:                   按 ESTIMATED\_IMAGE\_CHARS = 4800 字符估算

}
```

**要点**：



* 每类消息的 "计数字符" 口径不同（assistant 连 thinking 和工具参数都算）。

* 图像按固定 4800 字符折算（无法精确知道 token，给个保守估计）。

* 明确注释 "conservative (overestimates tokens)"——**宁可高估**，避免真的超窗口。

### 4.2 用量优先 `estimateContextTokens`



```
// 从最后一条有效的 assistant usage 往前，provider 返回过真实 usage 就用它，

// 只对 usage 之后的新消息做估算

const usageTokens = calculateContextTokens(usageInfo.usage);

for (let i = usageInfo.index + 1; i < messages.length; i++) {

&#x20; trailingTokens += estimateTokens(messages\[i]);

}

return { tokens: usageTokens + trailingTokens, ... };
```

**要点**：**真实 usage（provider 返回的 totalTokens）优先**，估算只用于 "最近一轮之后" 没有 usage 的部分。这是估算精度的关键 —— 不是从头估到尾，而是 "最后已知真实值 + 增量估算"。



```
export function calculateContextTokens(usage: Usage): number {

&#x20; return usage.totalTokens || usage.input + usage.output + usage.cacheRead + usage.cacheWrite;

}
```

注意：usage 里专门区分了 `cacheRead` / `cacheWrite`（提示词缓存 token），估算上下文要算上它们。



***

## 5. 上下文压缩 Compaction（★ 核心算法）

### 5.1 配置与触发



```
export const DEFAULT\_COMPACTION\_SETTINGS = {

&#x20; enabled: true,

&#x20; reserveTokens: 16384,   // 保留给"压缩 prompt + 摘要响应"的 token

&#x20; keepRecentTokens: 20000, // 保留最近 20000 token 的原始消息（不摘要）

};

export function shouldCompact(contextTokens, contextWindow, settings) {

&#x20; return contextTokens > contextWindow - settings.reserveTokens;

}
```

**触发逻辑**：`当前上下文 token > 模型窗口 - reserveTokens`。即：窗口还有 16K 余量时就开始压缩，保证压缩本身（要用 LLM 生成摘要）有足够空间运行。

### 5.2 切割点算法 `findCutPoint`（★）

目标：从最新往回走，保留约 `keepRecentTokens` 的最近消息，把更早的交给摘要。



```
function isCutPointMessage(message) {

&#x20; // 可在这些消息处切：user / assistant / bashExecution / custom / branchSummary / compactionSummary

&#x20; // 绝不可切：toolResult（工具结果必须跟在它的工具调用后面！）

&#x20; return message.role !== "toolResult";

}

function findCutPoint(entries, startIndex, endIndex, keepRecentTokens) {

&#x20; // 1. 从最新往旧累加消息估算 token

&#x20; // 2. 累加到 >= keepRecentTokens 时，找最近的"合法切割点"

&#x20; // 3. 若切割点落在 turn 中间 → 标记 isSplitTurn，并回溯找到该 turn 的起始 user 消息

}
```

**三个关键规则**：



1. **工具结果不可切割**——`toolCall` 和它的 `toolResult` 必须配对保留，否则模型看到结果却不知道调用是啥（或反之），对话就乱了。

2. **可以切在 assistant 消息上**—— 如果这条 assistant 带 tool\_calls，它后面的 toolResult 会跟着保留（因为切割点在其之前）。

3. **跨回合切割（isSplitTurn）**：如果超预算时切在了一个 turn 中间，就**只保留这个 turn 的 "后半段"（suffix）**，并把 "前半段（prefix）" 单独做一次摘要（`TURN_PREFIX_SUMMARIZATION_PROMPT`），保证被切开的 turn 上下文不丢。

### 5.3 摘要生成（结构化 + 增量更新）

压缩不是简单 "总结一下"，而是用**固定的结构化格式**（这样后续 LLM 能稳定消费）：



```
\## Goal

\## Constraints & Preferences

\## Progress

&#x20; \### Done / ### In Progress / ### Blocked

\## Key Decisions

\## Next Steps

\## Critical Context
```

关键点：



* **增量更新**：如果之前已经有压缩摘要（`previousSummary`），用 `UPDATE_SUMMARIZATION_PROMPT` 让模型 "在旧摘要基础上合并新信息"，而不是每次从头总结。长会话多次压缩时，摘要只增不减、信息不丢。

* **对话序列化**：把要摘要的对话 `serializeConversation` 成纯文本，用 `<conversation>...</conversation>` 标签包住 ——**防止模型把摘要任务当成 "继续对话"**。

* **摘要失败保护**：`stopReason === "length"`（摘要本身被截断）或结果含 toolCall 时，**拒绝落盘**（不完整的摘要不能作为检查点）。

* **maxTokens**：`min(0.8 * reserveTokens, model.maxTokens)`—— 压缩调用也留了 20% 余量。

### 5.4 文件操作追踪（★ 容易被忽略的精妙设计）



```
// 从被摘要消息的工具调用中提取 readFiles / modifiedFiles

const fileOps = extractFileOperations(messagesToSummarize, pathEntries, prevCompactionIndex);

// 压缩结束后把文件清单追加进摘要

summary += formatFileOperations(readFiles, modifiedFiles);
```

**为什么重要**：摘要把 "哪一步改了哪个文件" 这类细节压缩掉了，但**文件级操作是后续继续工作的关键**。pi 把这些信息单独提取、追加到摘要末尾（"Read Files: … / Modified Files: …"），并在下一次压缩时从旧 compaction entry 的 details 里继续累积。这样即使反复压缩，文件改动轨迹也不丢。

### 5.5 压缩结果



```
interface CompactionResult {

&#x20; summary: string;            // 生成的摘要（含文件清单）

&#x20; firstKeptEntryId: string;   // 保留的第一条原始消息 id（切割点）

&#x20; tokensBefore: number;       // 压缩前 token 数（用于统计）

&#x20; usage?: Usage;              // 摘要调用的 token 用量（计入成本）

&#x20; details?: { readFiles, modifiedFiles };  // 供下次压缩累积

}
```



* 压缩后**不删除旧条目**：新增一条 `compaction` 条目 + 保留 `firstKeptEntryId` 之后的消息。旧历史仍在 JSONL 里。

* `tokensBefore` / `usage` 用于评估 "压缩到底省了多少、花了多少"。

### 5.6 分支摘要（树形会话独有的压缩）

当用户在会话树里**跳到另一条分支**时（`branch-summarization.ts`）：



```
collectEntriesForBranchSummary(session, oldLeafId, targetId):

&#x20; // 1. 找旧分支与目标分支的"公共祖先"（common ancestor）

&#x20; // 2. 收集从旧叶子回溯到公共祖先的条目

&#x20; // 3. 按 token 预算（contextWindow - reserveTokens）从新到旧挑选

&#x20; //    注意：compaction/branch\_summary 条目即使超预算也尽量保留（它们小而关键）

&#x20; // 4. 生成结构化摘要，前缀加一句

&#x20; //    "The user explored a different conversation branch before returning here."
```

**要点**：离开一条分支不等于丢弃它 —— 生成 "分支摘要" 作为上下文，回来时不用重新读整条分支。这是树形会话 + 压缩的完整闭环：**分支可以丢，但分支的记忆（摘要）不丢**。



***

## 6. 工具系统（coding-agent/core/tools）

pi 的工具集刻意保持 "少而原子"：



| 工具                              | 说明               |
| ------------------------------- | ---------------- |
| `bash.ts` / `powershell.ts`     | shell 执行         |
| `read.ts` / `write.ts`          | 读 / 写文件          |
| `edit.ts` / `edit-diff.ts`      | 编辑（diff 方式，只改差异） |
| `find.ts` / `grep.ts` / `ls.ts` | 查找               |
| `truncate.ts`                   | 输出截断（防大输出爆上下文）   |
| `file-mutation-queue.ts`        | 文件变更队列           |

值得注意的细节：



* `file-mutation-queue`：并发工具调用可能同时改同一文件，pi 用队列串行化文件变更，避免写冲突。

* `output-accumulator`**&#x20;/&#x20;**`truncate`：命令输出是上下文爆炸的头号来源，工具层做了输出累积 + 截断治理。

* 每个工具包一层 `tool-definition-wrapper`，统一暴露 `execute(id, args, signal, onPartialResult)` 接口，正好接入 agent-loop 的三阶段管道。



***

## 7. Provider / 模型抽象（packages/ai）



* **70+ provider**：`providers/` 下每个 provider 一个文件（openai /anthropic/deepseek /google/mistral /xai/together /openrouter/qwen /kimi/minimax /moonshotai/nvidia /groq/fireworks /cerebras/huggingface /amazon-bedrock/google-vertex /azure/cloudflare /github-copilot/baseten /opencode/openai-codex /vercel-ai-gateway…）。

* 每个 provider 配套一个 `xxx.models.ts`（模型目录：模型名 → 上下文窗口 /maxTokens/ 价格 / 能力）。

* 统一的 `Model` 接口：`contextWindow`、`maxTokens`、`reasoning`、`usage`（含 `cacheRead`/`cacheWrite`）等。

* 流式统一：`stream-fn.ts` 把各家流式事件归一成 `text_start/delta/end`、`thinking_*`、`toolcall_*` 的统一事件 ——**上层的 agent-loop 完全不感知底层是哪家**。



***

## 8. Hooks / 扩展（coding-agent）

agent-loop 的 config 暴露了全套钩子点（这是 pi 扩展性的核心）：



```
getApiKey()          — 动态取 API key（支持过期 token）

transformContext()   — 发给 LLM 前改写上下文

convertToLlm()       — 内部消息 → LLM 消息

beforeToolCall()     — 工具执行前（可拦截 = HITL/审批落点）

afterToolCall()      — 工具执行后（可改写结果）

prepareNextTurn()    — 轮次间准备（压缩/换模型）

shouldStopAfterTurn()— 自定义停止条件

getSteeringMessages()— 排队消息

getFollowUpMessages()— 后续消息
```



* coding-agent 侧还有 `event-bus.ts`（事件总线）、`extensions/`（扩展系统）、`trust-manager.ts`（项目信任）、`output-guard.ts`（输出守卫）、`usage-totals.ts`（用量统计）。

* 扩展通过两阶段加载：**加载阶段只注册 → 绑定阶段注入运行时**（安全性隔离，这也是 V4 计划阶段 5 的原型）。



***

## 9. 设计哲学总结（学完该记住的）



1. **循环要薄，扩展要走钩子**：核心循环只做 "取消息→调模型→执行工具"，其余全部是钩子点。

2. **事件是产品**：一切 UI / 日志 / 审计 / 审批都挂在统一事件流上，而不是各写各的。

3. **消息模型比 LLM 消息更丰富**：内部用自定义角色（bashExecution/custom/compactionSummary），只在边界转换。

4. **token 估算 "保守高估 + 真实 usage 优先"**：不精算，但绝不低估。

5. **压缩是 "切 + 摘要 + 文件追踪" 三件事**：不是简单总结，切割规则保证工具配对、跨轮不裂、文件轨迹不丢。

6. **append-only 树形历史**：可 fork、可回溯、可审计，压缩也不破坏历史。

7. **截断即失败**：模型输出被截断时，宁可作废工具调用也不要执行残缺参数。



***

*整理时间：2026-09-02　来源：badlogic/pi-mono 源码精读（agent-loop.ts/compaction.ts/branch-summarization.ts 等）*