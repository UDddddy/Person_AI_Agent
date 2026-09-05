# 阶段 6 设计复盘：树形会话存储 + Compaction 上下文压缩

> 本文是阶段 6 开发完成后的设计决策复盘，既是学习笔记，也是面试讲项目时的核心素材。
> 覆盖：为什么要做树形存储、append-only 的设计哲学、安全压缩的切割点算法、三层架构分工、测试设计与测试抓到的真实缺陷。



***

## 一、阶段 6 要解决什么问题

阶段 6 之前，会话历史是**线性**的，有两套存储：



* `db.py` 的 messages 表：按 id 排序的扁平列表

* LangGraph Checkpoint：把整个 messages 列表存成快照

线性存储在短对话里没问题，长对话会撞上三堵墙：



| 问题      | 表现                                                            |
| ------- | ------------------------------------------------------------- |
| 不能分叉    | 想从第 3 轮换个方向问，只能删掉后面所有消息                                       |
| 不能时间旅行  | 无法 "切回某个历史状态继续聊"，只能往前翻记录                                      |
| 上下文窗口爆炸 | 对话越长 messages 越大，迟早超模型 token 上限；粗暴删前面的消息又可能拆散 "工具调用" 和 "工具结果" |

阶段 6 的解法：**树形结构**解决分叉 / 回溯，**Compaction**解决窗口爆炸。



***

## 二、三层架构：为什么拆成三个文件



```
app/

├── session\_store.py    ← 存储层：只管节点怎么存、怎么查（纯SQLite，不认识langchain）

├── compaction.py       ← 算法层：只管给定一条链在哪切、怎么压（纯函数，不碰数据库）

└── session\_adapter.py  ← 适配层：langchain消息 ↔ entry 互转
```

**依赖方向决定了可测性**：



* `compaction.py` 是纯函数，输入 list 输出 list，零 setup 就能单测。

* `session_store.py` 不依赖任何框架，只认 dict，以后换框架存储层零改动。

* `session_adapter.py` 是唯一同时认识两边的 "翻译官"，转换逻辑集中管理，不散落到各处。

这就是**关注点分离**：算法可独立测试、存储不绑定框架、转换集中管理。



***

## 三、树形存储：parent\_id + append-only

### 3.1 数据结构

线性存储每条消息记 "我排第几"（自增 id）。树形存储每条 Entry 记 "**我上一条是谁**"（parent\_id）：



```
线性：  \[msg1] → \[msg2] → \[msg3] → \[msg4]     只能一条线

树形：

&#x20;                \[msg1]

&#x20;                  |

&#x20;                \[msg2]

&#x20;                 /    \\

&#x20;            \[msg3]    \[msg3']   ← 从msg2分叉，两个版本并存

&#x20;              |

&#x20;            \[msg4]
```

核心字段：



| 字段             | 作用                                                   |
| -------------- | ---------------------------------------------------- |
| `id`           | uuid4 随机生成，不依赖顺序                                     |
| `parent_id`    | 指向上一条；根节点为 NULL                                      |
| `session_id`   | 会话标识；Fork 出新会话时用新 id                                 |
| `type`         | user / assistant / tool / system / compaction / fork |
| `content`      | 消息文本                                                 |
| `tool_calls`   | assistant 的工具调用，库里存 JSON 字符串                         |
| `tool_call_id` | tool 消息对应的调用 id（配对用）                                 |
| `metadata`     | 额外信息（token\_count、压缩审计等），JSON 字符串                    |

`tool_calls` / `metadata` 在库里是 JSON 字符串（SQLite 无原生 dict），读出来时 `_row_to_dict` 用 `json.loads` 还原。

### 3.2 append-only：永远不 UPDATE/DELETE

这是整个设计的灵魂。节点一旦写入，永远不改不删，所有变化都靠 "追加新节点" 表达。

三个理由：



1. **崩溃安全**：压缩 = 删除旧消息时，删到一半崩溃就永久丢历史；append-only 下压缩只是加一个摘要节点，崩了原始历史毫发无损。

2. **天然支持分支回溯**：正因为旧节点不删，才能从历史节点长新支，同时保留旧分支。

3. **可审计**：能查到 "被压缩过几次、每次压掉了哪些、原始叶子是哪条"，都存在摘要节点的 metadata 里。

代价是占点存储空间，但磁盘比上下文窗口便宜太多。

### 3.3 build\_chain：从叶子往根回溯



```
从当前叶子 → 取 parent\_id → 找父节点 → 再取 parent\_id

→ ...直到 parent\_id 为 NULL（到根）→ reverse 反转成根→叶子顺序
```

**为什么从后往前而不是从头往后？** 一个父节点可能有多个子节点（分叉了），从头走不知道选哪个分支；但从**一片确定的叶子**往回走，每个节点只有一个 parent，路径唯一。"你当前站在哪片叶子" 决定了你看到哪条历史。

初期用 `timestamp` 最新的节点 = 当前叶子（`get_leaf`）。

### 3.4 Fork 和时间旅行：本质是同一个机制

两者都是 "从某个历史节点往后追加新消息"。

**时间旅行（同会话内）**：



```
原链：  A → B → C

从B长新支：A → B → C      （C还在，没删）

&#x20;               \→ E      （E的parent\_id=B，时间戳更新）

当前叶子变成E，build\_chain 从E回溯：E→B→A，C不在当前链但仍在库里
```

代码就是 `append_entry(session_id, parent_id=B的id, ...)`，无需特殊函数。`travel_to` 只负责校验节点确实属于当前会话。

**Fork（跨会话）**：从 B 长出新 session\_id 的链。`fork_session` 创建 `type='fork'` 标记节点，parent\_id 指向旧会话的 B。新会话 `build_chain` 回溯时顺着指针走到旧历史 —— 新分支天然继承分叉点前的全部上下文，之后独立发展。



```
旧会话 s1: A → B → C

&#x20;               ↓ (fork标记parent指向B)

新会话 s2:      \[fork] → D

build\_chain(s2): D → fork → B → A  （自动带上旧历史）
```



***

## 四、Compaction：难点全在 "在哪下刀"

### 4.1 token 估算：零依赖够用即可

`estimate_tokens`：中文字符按 1.5 token / 字，其余按 0.25 token / 字符（约英文 4 字符 1 token）。

为什么不用精确的 tiktoken？计划定了 "零新依赖"，且压缩决策只需知道 "大概超没超"，阈值 2000 下估算误差几十个 token 不影响判断。代码注释标注了 "生产环境应替换为真实 tokenizer"—— 学习项目用够用方案，但把升级点标清楚。

### 4.2 安全切割点：阶段 6 最核心的算法

**铁律：assistant 的 tool\_call 和对应的 tool 结果必须同生共死，要么都保留，要么都进摘要。**

错误压缩的后果：



```
摘要 + \[tool\_call: calculator(1+1)]   ← LLM只看到"我调了工具"

&#x20;                                     ← 结果"2"被切掉了！

LLM：？？我调了工具但结果呢？→ 行为异常/重复调用
```

`find_safe_cut_point` 用 `pending`**（待配对集合）** 保证：



```
从头扫描，累计token：

\- 遇到 assistant 带 tool\_calls → 把每个 tool\_call id 放进 pending（"等结果"）

\- 遇到 tool 消息 → 把 tool\_call\_id 从 pending 移除（"账清了"）

\- pending 为空 = 所有调用都有结果 = 这里可以安全下刀

\- pending 非空 = 有调用没等到结果 = 禁止切

累计超预算时，返回"最后一个 pending 为空的位置"
```

以 max=25 为例：



| 扫描到                        | 累计 token                  | pending   | 能切吗          |
| -------------------------- | ------------------------- | --------- | ------------ |
| user                       | 7                         | 空         | last\_safe=1 |
| assistant (2 个 tool\_call) | 25                        | {tc1,tc2} | 不能           |
| tool (tc1 结果)              | 25                        | {tc2}     | 不能           |
| tool (tc2 结果)              | 25                        | 空         | last\_safe=4 |
| assistant                  | 28>25 → 超，返回 last\_safe=4 |           |              |

max=20 时，扫描到 assistant（累计 22>20）就超，last\_safe 只有 1，于是只切第 1 条 ——**宁可少压，也不在配对中间切**。安全优先于压缩率。

### 4.3 append-only 下怎么压缩：摘要新根 + 保留部分重放

线性存储压缩 = 删前面的消息。我们不删，做法：



```
压缩前：  A → B → C → D → E    （A-C要压，D-E保留）

第1步：追加 compaction 摘要节点，parent\_id=None（成为"新根"）

第2步：把 D、E 复制一份重新追加（D'、E'），parent链到摘要后面

&#x20;        摘要 → D' → E'

旧 A→B→C→D→E 原封不动还在库里（可审计），

当前叶子是 E'，build\_chain 从 E' 回溯只看到：摘要 → D' → E'
```

**为什么保留部分要复制重放而不是复用 D、E？** 因为 D 的 parent\_id 指向 C（旧链），不复制就从摘要走不到 D。append-only 不能改 D 的 parent\_id，只能追加副本 D'，parent 指向摘要。副本 metadata 记 `replayed_from: 原D的id`，可追溯原件。

### 4.4 手动 /compact 为什么要 force 参数

最初 `compact_session` 不管触发来源都先检查 "超没超阈值"，结果手动发 `/compact` 时因为对话太短（没到 2000）就不压了。但手动压缩是用户**明确指令**，不该被阈值拦住。于是加 `force=True` 跳过阈值检查 —— 自动压缩看阈值，手动压缩听命令。同一个函数，两种触发语义不同。

### 4.5 三种压缩触发



| 触发方式               | 实现                                                                                                   |
| ------------------ | ---------------------------------------------------------------------------------------------------- |
| 手动 `/compact [摘要]` | `force=True` 立即压缩                                                                                    |
| 阈值自动               | 每次 `run_graph_agent` 入口算链 token，超 2000 自动压（学习版用固定摘要，生产应调 LLM 生成自然语言摘要）                               |
| 溢出恢复重试             | `agent_node` 里 try 调 LLM，错误含 context\_length/maximum context/context window 时，保留 system + 最近 4 条重试一次 |



***

## 五、主链路集成（graph\_agent 改造）

### 5.1 树形存储 vs Checkpoint 的分工



* **历史内容**归树形存储：每次进 `run_graph_agent`，`build_chain` 重建历史 → `chain_to_messages` 转 langchain 消息 → 注入图。图不再依赖 Checkpoint 记忆历史。

* **Checkpoint 代码保留**，但不再承担历史职责（它更擅长存图运行时状态，如 interrupt 挂起）。

### 5.2 数据流



```
用户发消息

&#x20; ↓

run\_graph\_agent:

&#x20; build\_chain(session\_id)  ← 从树形库读历史链

&#x20; → chain\_to\_messages      ← 翻译成 langchain 消息

&#x20; → \[system] + 历史 + \[新HumanMessage] 组成 inputs

&#x20; → persist\_messages(新消息)  ← 新用户消息先落盘

&#x20; ↓

图运行：

&#x20; agent\_node: call\_llm → AIMessage → persist\_messages 落盘

&#x20; tool\_node:  执行工具 → ToolMessage → persist\_messages 落盘

&#x20; （agent→tools→agent 循环，每步产出即时写树，parent自动接当前叶子）
```

**为什么节点里用&#x20;**`get_leaf`**&#x20;找 parent 而不是手动传 id？** 图严格顺序执行，一个节点执行时库里最新叶子必然是它的上一条消息。`persist_messages` 内部自动 `get_leaf` 拿 parent，节点代码不用操心 id 传递，一批多条 ToolMessage 也自动串成链。

### 5.3 system prompt 也必须进树

最初只把 HumanMessage 落盘，system 只在第一轮注入 inputs。结果第二轮 `build_chain` 重建历史时树里没有 system 节点，LLM 从第二轮起丢了系统人设。修复：首次会话把 SystemMessage 也 persist 成 `type='system'` 的根节点。**教训：凡是希望 LLM 每轮都看到的消息，都必须进主存储，不能只在内存临时塞。**

### 5.4 节点里 `state.get("session_id")` 才落盘



```
sid = state.get("session\_id")

if sid:

&#x20;   persist\_messages(sid, \[ai\_msg])
```

单元测试直接调节点函数、传无 session\_id 的假 state 时，不会往真实库写垃圾。真实运行（有 session\_id）才持久化 —— 让生产代码对测试友好，而不是为测试改业务逻辑。



***

## 六、测试设计

阶段 6 测试在 `test/test_stage6.py`，共 28 个，分三类对应三层架构。

### 6.1 分层测试



| 测试类                | 数量 | 方式                      |
| ------------------ | -- | ----------------------- |
| TestCompaction     | 7  | 纯算法，构造 list 直接测，零 setup |
| TestSessionStore   | 14 | 每个测试用 tmp\_path 隔离独立数据库 |
| TestSessionAdapter | 7  | 测 langchain ↔ entry 翻译  |

### 6.2 关键 fixture：tmp\_path + monkeypatch 隔离数据库



```
@pytest.fixture

def store(tmp\_path, monkeypatch):

&#x20;   db\_path = tmp\_path / "test\_sessions.db"

&#x20;   monkeypatch.setattr(ss, "DB\_PATH", db\_path)  # 临时替换模块级数据库路径

&#x20;   init\_store()
```



* `tmp_path`：pytest 给每个测试分配独立临时目录，测试间物理隔离，跑完自动清理。

* `monkeypatch.setattr`：临时改 `session_store.DB_PATH`，测试结束自动还原，绝不碰真实库。

### 6.3 值得学的测试手法



1. **验证不变量而非具体值**（test\_safe\_cut\_preserves\_tool\_pair）：不直接断言切点等于几，而是把切点之前的消息拿出来重跑 pending 配对逻辑，断言 "被压部分没有未配对工具调用"。算法怎么调，这条铁律永远成立，测试就永远有效。

2. **时间旅行双断言**（test\_time\_travel\_grows\_new\_branch）：



```
assert c\["id"] not in 当前链       # 旧分支被切换走了

assert get\_entry(c\["id"]) is not None  # 但物理上没删
```

两条合起来才完整表达 append-only 时间旅行语义。



1. **Fork 断言跨会话回溯通了**：不是只断言建了 fork 节点，而是 `build_chain(新会话)` 里能找到旧会话消息内容 —— 证明跨会话指针真的通了。

2. **端到端 mock LLM 验证历史重建**：假 `call_llm` 记录 "收到几条消息、什么类型"，第二轮断言 LLM 确实收到第一轮的 user+assistant—— 证明 "落盘→重建→再注入" 整条链路通，而不只是单元函数各自正确。

### 6.4 测试抓到的 4 个真实问题



| 问题                      | 根因                     | 修复                              |
| ----------------------- | ---------------------- | ------------------------------- |
| 第二轮 LLM 丢 system prompt | system 只注入 inputs 没落盘  | 首次会话 persist SystemMessage      |
| 手动 /compact 短对话不压       | compact\_session 受阈值限制 | 加 force 参数，手动 force=True        |
| tool\_call 构造报错         | 测试数据缺必填 args 字段        | 补 args，记住 ToolCall=name+args+id |
| tool\_calls 全等断言失败      | LangChain 自动补 type 字段  | 改逐字段断言 name/args/id             |

**教训：对框架返回对象做全等比较很脆弱，断言关心的关键字段更可靠。**



***

## 七、设计哲学一句话总结

> 用 "只追加" 的树代替 "会覆盖" 的线，用 "配对感知的安全切割" 代替 "粗暴截断"，用 "摘要新根 + 重放" 在不丢历史的前提下缩短上下文。

三个可迁移到任何 Agent 项目的通用能力：



1. **parent\_id 回溯**：从叶子唯一确定一条历史链

2. **待配对集合找安全点**：保证工具调用与结果不被拆散

3. **append-only 下的重放式压缩**：不删旧数据，用新根 + 副本表达压缩后的视图



***

## 八、面试要点速查

被问到 "你这个项目的会话管理怎么做的" 时，可以按这个顺序讲：



1. **痛点**：线性存储不能分叉、不能回溯、长对话超窗口

2. **方案**：append-only 树形存储（parent\_id）+ Compaction 安全压缩

3. **核心算法**：pending 待配对集合保证工具调用 / 结果不被拆散

4. **压缩实现**：摘要新根 + 保留部分重放（不删旧数据，可审计）

5. **三种触发**：手动 /compact、阈值自动、溢出恢复重试

6. **测试亮点**：tmp\_path 隔离、不变量验证、端到端 mock 验证历史重建

7. **设计权衡**：零依赖 token 估算（够用即可，标注升级点）、Checkpoint 保留做图状态而非历史



***

*文档生成时间：阶段 6 完成后・全量测试 89 passed*