# OpenAI Codex 架构设计学习笔记

> 对象：[openai/codex](https://github.com/openai/codex)（"Lightweight coding agent that runs in your terminal"）· Rust 实现
> 说明：本文基于对 `codex-rs` workspace 源码的精读整理（重点：core / tools / compact / context_window / token_budget / approvals / thread-store / rollout）。所有函数名、常量、阈值均来自真实源码。

---

## 0. 一句话定位

Codex 是一个 **Rust 写的、面向"执行安全"与"长对话治理"的终端编码 Agent**。与 pi 相比，它多出来的复杂度几乎全部集中在两件事上：**怎么安全地执行任意命令**（沙箱 + 审批 + 执行策略），和**怎么在一个很长的会话里精准管理 token**（窗口 + 预算 + 压缩）。它的核心是一个极薄、但被大量 `crate` 支撑的 turn 循环。

---

## 1. 总体架构：100+ crates 的 workspace

`codex-rs` 下按能力拆成 100+ 个独立 crate，按职责可归纳为 7 层：

```
┌──────────────────────────────────────────────────────┐
│ 入口层      cli · app-server · tui · mcp-server       │
├──────────────────────────────────────────────────────┤
│ 核心层      core（rollout/turn/agent/thread_manager） │
├──────────────────────────────────────────────────────┤
│ 工具层      tools（registry/router/orchestrator/…）   │
├──────────────────────────────────────────────────────┤
│ 执行与安全  unified_exec · exec · sandboxing ·        │
│             execpolicy · shell · process-hardening     │
├──────────────────────────────────────────────────────┤
│ 会话持久化  thread-store · rollout（JSONL+SQLite）     │
├──────────────────────────────────────────────────────┤
│ 模型抽象    model-provider · codex-api · client        │
├──────────────────────────────────────────────────────┤
│ 扩展+基建   hooks · plugins · mcp · skills · memories │
│             otel · analytics · config · secrets        │
└──────────────────────────────────────────────────────┘
```

**关键 crates**：
- `core`：Agent 主逻辑（rollout、turn、session、context、compact、tools 编排、approvals、elicitation）
- `tools`（独立 crate）：工具定义与注册
- `rollout`：**执行记录的持久化层**（JSONL 反向扫描 + 索引 + SQLite）
- `thread-store`：会话/线程存储抽象
- `model-provider`：Provider 抽象（含 amazon_bedrock）
- `hooks` / `plugin` / `mcp` / `skills` / `memories`：扩展体系
- `sandboxing` / `exec` / `execpolicy` / `unified_exec`：执行安全体系

---

## 2. 会话模型：thread → turn → step（session/ 目录）

codex 的会话不是"一条消息列表"，而是一个有结构的层次：

```
Thread（线程，对应一次会话）
└── Turn（一轮用户交互，含多个 step）
    └── Step（一次"调模型 + 执行工具"的子步）
```

核心文件（`core/src/session/`）：
- `session.rs`：会话本体（持有历史、状态、token 用量、world_state）
- `turn.rs` / `turn_context.rs` / `turn_input.rs`：turn 生命周期与上下文
- `step_context.rs` / `step_settings.rs` / `step_activation.rs`：step 管理与激活
- `world_state.rs`：世界状态（压缩/换窗口时保留的"快照"）
- `input_queue.rs`：输入队列
- `turn_suspension.rs`：turn 暂停（配合审批/澄清）
- `multi_agents.rs`：多 Agent
- `context_window.rs` / `token_budget.rs`：窗口与预算（见下文）

**设计要点**：把"一轮"再细分出 `step`，是因为一个 turn 里可能多次"模型→工具"循环。step 让每个子步的 token 用量、执行结果、失败重试都能独立追踪——这是审计和预算管理的基础。

---

## 3. 上下文组装：context fragments（context/ 目录）

codex 不把系统提示词写成一大坨，而是拆成**大量小的"上下文片段"（context fragment）**，按条件注入。`core/src/context/` 下每个文件是一个片段：

```
base_instructions           基础指令
developer_instructions      开发者指令
environment_context         环境上下文
permissions_instructions    权限说明
token_budget_context        当前 token 预算状态（★）
compaction_summary          压缩摘要
current_time_reminder       当前时间提醒
model_switch_instructions   模型切换说明
multi_agent_*              多 Agent 模式/角色指令
inter_agent_message         Agent 间消息
subagent_notification       子 Agent 通知
guardian_*                  guardian 自动审查相关
world_state/                世界状态片段
```

**要点**：
- 每个片段是一个独立模块，知道自己"什么条件下该出现、怎么渲染"。
- 对模型而言，最终收到的是"按当前状态拼装好的系统提示词"；对工程而言，加一个新能力 = 加一个新片段，不改主逻辑。
- 这与 pi 的"transformContext 钩子"是同一思想的不同实现：**上下文是"组装"出来的，不是写死的**。

---

## 4. 窗口管理（context_window.rs）★

### 4.1 窗口状态

```rust
struct ContextWindowTokenStatus {
    active_context_tokens: i64,            // 当前全部活跃上下文 token
    auto_compact_scope_tokens: i64,        // 计入压缩阈值范围的 token
    auto_compact_scope_limit: Option<i64>, // 压缩阈值
    full_context_window_limit: Option<i64>,// 模型全窗口硬上限
    base_window_tokens_remaining: Option<i64>, // 剩余可用的 token（取两者的较小值）
    full_context_window_limit_reached: bool,
    token_limit_reached: bool,
}
```

### 4.2 两种计数范围（AutoCompactTokenLimitScope）

```
Total（总量）：
    scope_tokens = 全部活跃上下文
BodyAfterPrefix（前缀之后）：
    scope_tokens = 活跃上下文 - 初始前缀的 prefill token
```

**为什么有两种**：LLM 的**前缀缓存（prefix cache）**意味着"前缀部分"每次请求都命中缓存、便宜且算作一次性的。`BodyAfterPrefix` 模式只统计"初始前缀之后新增的 token"作为压缩的度量——这样长前缀不会每次都触发压缩，更贴合"真正新增了多少"。

### 4.3 硬上限 + 缓冲

```rust
// 模型全窗口 × 有效百分比 = 硬上限（不可突破）
full_context_window_limit =
    model_info.resolved_context_window() * model_info.effective_context_window_percent / 100;

// 压缩阈值可加"兜底缓冲"，达到缓冲后的值才强制压缩
buffered_auto_compact_limit = auto_compact_scope_limit + fallback_buffer_tokens;

token_limit_reached = buffered_auto_compact_limit 已到 || full_context_window_limit 已到;
```

**三层防线的骨架**（详见下一节）：
1. `full_context_window_limit`：模型窗口 × 百分比（如 90%），硬上限。
2. `auto_compact_scope_limit`（+缓冲）：到点触发压缩。
3. `base_window_tokens_remaining`：剩余量用于"提醒模型"。

---

## 5. Token 计算与预算（token_budget.rs）★

### 5.1 TokenBudgetConfig（分层提醒）

```rust
struct TokenBudgetConfig {
    reminder_threshold_tokens: Option<i64>,          // 低于多少 token 就提醒模型
    reminder_message_template: Option<String>,       // 提醒文案模板
    guidance_message: Option<String>,                // 使用引导
    auto_compact_fallback_prompt: Option<String>,    // 自动压缩兜底提示
    auto_compact_fallback_buffer_tokens: Option<i64>,// 压缩兜底缓冲
}
```

配置来源有优先级：**用户显式配置 > 模型自带默认值**（`resolve_token_budget` 会优先采用模型厂商为该模型提供的默认 token 预算）。

### 5.2 三层防线（★）

```
第 1 层  模型全窗口硬上限（context_window）        → 无论如何都不能超
第 2 层  auto_compact 阈值（可加缓冲）             → 到点自动压缩
第 3 层  剩余 token 提醒（token_budget）           → 让模型自己省着用
```

**第 3 层的实现**（`maybe_record`，每 turn 调用）：

```rust
// 1) 剩余 token 低于提醒阈值 → 注入"上下文快满了"片段
if base_window_tokens_remaining <= reminder_threshold_tokens {
    record_conversation_items( TokenBudgetReminder { 剩余量 } );
}

// 2) 剩余为 0 且允许兜底 → 注入"自动压缩兜底提示"
if base_window_tokens_remaining == 0 {
    record_conversation_items( AutoCompactFallbackPrompt { ... } );
}
```

**两个关键机制**：
- **`claim_token_budget_reminder` / `claim_auto_compact_fallback` 状态去重**：同一个提醒每个 turn 只注入一次，不会反复刷屏。
- 提醒是作为**上下文片段（ContextualUserFragment）注入历史**的，模型在下一轮就能看到"我的上下文快满了，请精简输出"——是**让模型主动配合**，而不是只靠系统强制压缩。

### 5.3 其他 token 相关

- `approx_token_count` / `truncate_text`（`codex_utils_output_truncation`）：压缩时对超预算的用户消息按 token 截断。
- `recompute_token_usage`：压缩后重算整个会话的 token 用量。
- 每次模型响应都会 `update_token_usage_info` 记录真实 usage（含 cached_input_tokens / cache_write_input_tokens）。

---

## 6. 上下文压缩（compact.rs + compact_token_budget.rs）★

codex 有**两种压缩实现**，都走统一的"压缩生命周期"（都有 pre/post hooks、都会产生 `ContextCompaction` 条目、都上报 analytics）。

### 6.1 实现 A：模型摘要压缩（compact.rs，策略名 Memento）

**流程**（`run_compact_task_inner_impl`）：

```
1. 跑 pre-compact hooks（可中止）
2. 把专门的压缩 prompt（SUMMARIZATION_PROMPT）作为 user input
3. 复用当前历史，调 LLM 生成摘要（流式 drain_to_completed）
4. 出错处理：
   - ContextWindowExceeded → 从历史最开头移除最旧项（保留前缀缓存），重试
   - SessionBudgetExceeded → 报错
   - 流错误 → 指数退避（backoff）重试，最多 stream_max_retries 次
5. 组装新历史：保留最近用户消息（≤20,000 token）+ 摘要垫底
6. 跑 post-compact hooks（可中止）
7. 推进压缩窗口编号，重算 token，上报 analytics
```

**摘要 = 专门的压缩 prompt 让模型总结**，摘要文本形如 `SUMMARY_PREFIX + 最后一条 assistant 消息`。

**重建历史的关键**（`build_compacted_history`）：

```rust
const COMPACT_USER_MESSAGE_MAX_TOKENS: usize = 20_000;

// 从最新往旧收集用户消息：
//  - 每条的 token 用 approx_token_count 估算
//  - 累计达到 20,000 上限就停
//  - 最后一条超预算的，用 truncate_text 按剩余 token 截断
//  - 助手消息/工具消息全部丢弃（被摘要吸收）
// 最后把压缩摘要（CompactionSummary）作为最后一条 user 消息垫底
```

**初始上下文注入**（`InitialContextInjection`）：
- `DoNotInject`：不重新注入（等下一个正常 turn 重新注入完整初始上下文）。
- `BeforeLastUserMessage`：把初始上下文插到**最后一个真实用户消息之前**——因为模型训练要求压缩摘要必须是历史最后一条，初始上下文要放在它上面。

**两个重要工程细节**：
- **保留用户消息而不是助手消息**：用户原话最值得保留，模型输出可被摘要替代。
- **出错先删最旧**：压缩本身超窗口时，从开头移除最旧项（`history.remove_first_item()`）——因为前缀缓存基于前缀，删开头最不影响缓存命中。

### 6.2 实现 B：token 预算压缩（compact_token_budget.rs）

```rust
// 跳过模型/服务器摘要，直接安装一个全新的上下文窗口
sess.start_new_context_window(step_context, world_state).await;
```

- 适用于"token 预算触发的压缩"（`CompactionTrigger::Manual`/`Auto`）。
- **不调 LLM**，直接把历史清掉、只保留 `world_state`（世界状态快照）作为新窗口的基底。
- 仍然走完整的压缩生命周期（hooks、ContextCompaction 条目、analytics），所以从外部看和"摘要压缩"行为一致。

**两级策略的意义**：会话太长时，优先用"token 预算压缩"快速换窗（便宜、快）；需要保留语义时用"模型摘要压缩"（贵、慢、但信息保真）。

### 6.3 压缩的完整可观测性

```rust
struct CompactionAnalyticsAttempt {
    trigger, reason, implementation, phase,
    active_context_tokens_before,   // 压缩前 token
    started_at, duration_ms,
}
// track() 上报：before/after token、摘要 token、缓存 token、耗时、状态
```

压缩是**可度量的一等公民**：每次压缩都记录触发原因、前后 token 差、耗时、缓存命中——"压缩到底值不值"有数据说话。

---

## 7. 会话与执行持久化（thread-store + rollout crate）★

codex 把"会话存储"和"一次执行的记录"分开：

### 7.1 thread-store（会话抽象）

```
thread-store/src/
├── store.rs              # 存储 trait（统一接口）
├── in_memory.rs          # 内存实现
├── local/                # 本地实现
├── live_thread.rs        # 活跃线程
├── queue_store.rs        # 队列存储
└── thread_sections.rs    # 线程分段
```

用 **trait 抽象**统一多种后端（内存/本地/SQLite），业务代码只依赖 `store` trait——换存储不碰逻辑。

### 7.2 rollout crate（执行记录持久化）

```
rollout/src/
├── reverse_jsonl_scanner.rs   # 反向扫描 JSONL（从文件末尾往前读）
├── seekable_reader.rs         # 可寻址读取
├── session_index.rs           # 会话索引
├── rollout_reference_index.rs # 执行引用索引
├── state_db.rs                # SQLite 状态库
├── compression.rs             # 记录压缩
├── metadata.rs / ordinal.rs   # 元数据/序号
└── maintenance.rs             # 维护
```

**要点**：
- 每次执行（rollout）以 JSONL 追加记录。
- **反向扫描 + 可寻址读取**：长会话读"最新几条"不用扫描整个文件，直接从尾部反向读。
- **索引 + SQLite 状态库**：快速定位历史记录，而不依赖全量加载。
- 有独立的 `compression` 模块治理记录文件本身的体积。

---

## 8. 工具调用系统（core/src/tools/）★

工具层结构（`core/src/tools/`）：

```
registry.rs        工具注册表（哪些工具可用）
router.rs          工具路由（按名字分发）
orchestrator.rs    工具编排（一次调用多个工具怎么组织）
handlers/          工具处理器（具体实现）
runtimes/          工具运行时（apply_patch / unified_exec 等）
approvals.rs       审批（★ 见下节）
parallel.rs        并行执行
lifecycle.rs       工具生命周期
events.rs          工具事件（tool_dispatch_trace 追踪）
spec_plan.rs       工具调用计划
context.rs / sandboxing.rs / network_approval.rs
hook_names.rs      钩子名枚举
```

**工具生命周期**：`registry`（注册）→ `router`（路由）→ `orchestrator`（编排）→ `handlers`（执行）→ 每个环节都可被 `approvals`（审批）、`hooks`（钩子）、`events`（事件）插入。

这与 pi 的"三阶段管道（prepare/execute/finalize）"本质一致，但 codex 拆得更细：把"注册/路由/编排"也独立出来，且把**审批（approvals）和沙箱（sandboxing）作为一等公民**内嵌在工具执行链路里。

---

## 9. 审批系统 HITL（approvals.rs）★

这是 codex 相对 pi 最大的增量。所有需要人类（或自动审查者）批准的动作被枚举为：

```rust
enum ApprovalAction {
    ExecCommand { command, cwd, sandbox_permissions, justification, ... },
    WriteStdin { process_id, input, ... },   // 向终端写输入
    ApplyPatch { files, patch, ... },          // 应用补丁
    McpToolCall { server, tool_name, arguments, ... },
    NetworkAccess { host, port, protocol, ... },
    RequestPermissions { ... },
}
```

### 9.1 三级审批优先级（★）

```rust
// 注释原文：Approval precedence is:
// 1. Hooks
// 2. If StrictAutoReview || Guardian enabled, then Guardian. Else, user.
let resolution = match run_permission_request_hooks(...).await {
    Some(Allow)  => 批准（来源：Hook）
    Some(Deny)   => 拒绝（来源：Hook）
    None         => request_reviewer_approval(...)  // 降级到下一级
};
```

```
第 1 级  Hooks（配置策略规则）   → 规则命中就自动批/拒
第 2 级  Guardian（模型自动审查）→ StrictAutoReview 或启用了 Guardian 时
第 3 级  User（人工确认）        → 兜底
```

**设计意图**：**尽可能少打扰人**。能靠规则处理的交给规则，能靠模型审的交给 Guardian，只有都无法决定时才弹给用户。每个审批请求的最终决策来源被记录（`ToolDecisionSource: Config / AutomatedReviewer / User`），写进 telemetry 做审计。

### 9.2 审批缓存（with_cached_approval）

```rust
// 同一类命令/补丁审批过后缓存，后续直接放行，避免重复询问
ApprovalCacheKey::ExecCommand({ executable, command, cwd, tty, sandbox_permissions, ... })
ApprovalCacheKey::ApplyPatch({ environment_id, path })
```

- 按"规范化后的命令 + cwd + 沙箱权限"做键。
- 用户批准过一次"在这个目录跑 npm test"，同类操作不再反复问。

### 9.3 决策类型与结果

```rust
enum ReviewDecision { Approved, Denied, TimedOut, Abort, NetworkPolicyAmendment, ... }
```

- `TimedOut`：自动审查超时 → 按拒绝处理。
- `Abort`：用户中止 → 抛 `TurnAborted`，整个 turn 取消。
- 被拒时返回 `ToolError::Rejected(rejection)` 作为工具错误消息，**让模型知道"没被批准、并拿到原因"**，从而自我调整。

---

## 10. 澄清（elicitation.rs）

```rust
// ElicitationService：用户澄清的协调器
// 注册计数（outstanding），>0 时暂停"工具结果投递"
// 所有澄清完成后才恢复（watch channel 通知）
```

- Agent 在信息不足时可以**暂停执行、向用户提问**，而不是瞎猜。
- 用引用计数支持"多个澄清并发"：全部结束才恢复。
- 这是"编码 Agent 的安全对话"关键能力：**执行不确定动作前，先问清楚**。

---

## 11. 扩展体系：hooks / plugins / MCP / skills / memories

```
hooks/         hooks 引擎
   ├── registry.rs     钩子注册
   ├── declarations.rs 钩子声明
   ├── engine/         钩子执行引擎
   ├── events/         钩子事件
   ├── mcp.rs          MCP 钩子
   └── config_rules.rs 配置规则
plugins/       插件系统
mcp/  mcp-server/   MCP（Model Context Protocol）——接入外部工具/资源
skills/         技能（可给模型动态注入的专业能力）
memories/       记忆
```

与 pi 的"事件 + 钩子"思路一致，但 codex 把 hooks 做成了**独立的 engine crate**，并且**深度绑定 MCP**——这也是它能够接入大量外部生态的关键。

---

## 12. 对学习项目的启示（与 pi 对照）

| 维度 | pi 的做法 | codex 的做法 | 对本项目最值得学的 |
|---|---|---|---|
| 核心循环 | 双嵌套循环 + 事件流 | thread→turn→step 分层循环 | 用事件流驱动一切 |
| 上下文 | transformContext 钩子 | context fragments 按条件注入 | **fragment 组装式系统提示词** |
| token 计算 | chars/4 保守估算 + usage 优先 | 真实 usage + 缓存 token 细分 | **真实 usage 优先，估算兜底** |
| 窗口管理 | shouldCompact 触发 | **三层防线**（硬上限/压缩阈值/提醒） | **把"提醒模型省着用"做进上下文** |
| 压缩 | 切割点 + 结构化增量摘要 + 文件追踪 | 模型摘要 / token 预算两级 + 初始上下文注入 | **保留用户消息（20k）+ 摘要垫底** |
| 会话 | append-only 树形 JSONL | thread-store trait + rollout 持久化 | append-only 不可变历史 |
| 工具 | 三阶段管道（prepare/execute/finalize） | registry/router/orchestrator + 审批 | **把审批做成工具链路的插槽** |
| HITL | beforeToolCall 钩子拦截 | **三级审批（Hook→Guardian→User）+ 缓存** | **审批优先级降级 + 审批缓存** |
| 安全执行 | 无 | 沙箱 + execpolicy + 统一执行 | （个人项目可简化为"审批 + 白名单"） |
| 澄清 | 无 | elicitation（暂停-恢复） | **信息不足时主动提问** |
| 多 provider | 70+ provider | provider trait + bedrock | 抽象边界 + 模型目录 |

### 三个"如果只能学三件事"的结论

1. **把 token 治理做成三层防线**：硬上限 → 压缩阈值 → 剩余量提醒。尤其"提醒"这一层，是让模型主动配合、成本最低收益最高的一招。
2. **审批要做成"能降级的链条"**：规则（hooks）→ 自动审查 → 人工。每次审批有缓存、有审计。个人项目可以先从"危险命令白名单 + 人工确认"起步，但把优先级结构和缓存设计预留好。
3. **压缩的工程质量在"切割规则"**：工具结果不可切、保留用户消息、摘要结构化且增量、出错先删最旧——这些细节决定压缩后对话还能不能继续，而不是简单地"总结一下就完事"。

---

*整理时间：2026-09-02　来源：openai/codex 源码精读（core/src/compact.rs / context_window.rs / token_budget.rs / tools/approvals.rs / elicitation.rs 等）*
