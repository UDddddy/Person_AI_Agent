# 阶段 2 学习文档：多轮会话与 SQLite 记忆

> 适用阶段：阶段 2｜V2 多轮会话 + SQLite 记忆（约 1 周）
> 技术栈：会话模型（session_id）、SQLite、上下文注入、数据库事务与连接管理
> 配套项目代码：`app/db.py`、`app/agent.py`、`app/main.py`、`app/schema.py`、`test/conftest.py`

---

## 0. 这份文档怎么用

阶段 2 解决一个你亲手验证过的痛点：**Agent 不记得上一轮聊了什么**。方法是用 `session_id` 区分会话、用 SQLite 持久化消息。这份文档让你搞懂三个层次：

1. **为什么**会有"失忆"问题（无状态架构的本质）
2. **怎么做**（session_id + 数据库）
3. **底层**：SQLite 到底怎么存的、连接和事务是怎么回事、为什么这么写

---

## 1. 先看清问题：Agent 为什么会"失忆"

回到阶段 0/1 的认知：**大模型是无状态的**，它每次只看到你这次传给它的 `messages`。你阶段 1 的 `run_agent` 每次调用都是：

```python
messages = [system, {"role": "user", "content": user_message}]   # ← 每次都从零开始！
```

所以用户上一轮说"请计算 1+1"，这轮说"刚才的结果加 10"，模型根本不知道"刚才"是 2。**失忆不是模型的错，是你的 `messages` 没有携带历史。**

### 解决思路（一句话）

> **把之前聊过的话存下来，下一次请求时重新拼进 `messages`。**

这就引出两个技术点：
- **会话模型**：怎么区分"这是哪一场对话"→ `session_id`
- **持久化**：把消息存在哪、怎么存取 → SQLite

---

## 2. 会话模型：session_id 到底是什么

### 你的代码

```python
# schema.py
class ChatRequest(BaseModel):
    message: str
    session_id: str = "default_session"   # 客户端每次请求带上"这场对话的钥匙"
```

### 为什么需要它

同一个 Agent 服务可能同时服务很多用户/很多场对话。**如果所有消息混在一个大池子里，谁是谁的上下文就全乱了。** `session_id` 就是"对话的隔离 ID"：

- 同一个 `session_id` 的所有请求 → 属于**同一场对话**，能共享历史
- 不同 `session_id` → **彼此隔离**，互不干扰

> **对照你的项目**：这个 `session_id` 的概念，到阶段 3 会以 `thread_id` 的名字再出现一次（LangGraph 的 Checkpoint）。**它们是同一个思想**：一把钥匙锁住一场会话的状态。你在阶段 2 亲手实现了这个思想，阶段 3 会看到框架帮你做。

---

## 3. SQLite：为什么是它

### 3.1 为什么选 SQLite（而不是 MySQL/文件/内存）

| 方案 | 特点 | 问题 |
|---|---|---|
| 内存里存 dict | 最简单 | **服务一重启全丢** |
| 存 JSON 文件 | 简单 | 并发读写会坏、无查询能力 |
| MySQL/PostgreSQL | 生产级 | 要装服务器、配置复杂，个人阶段 2 太重 |
| **SQLite** | 零配置、单文件、嵌入式 | ✅ 正好 |

**SQLite 的定位**：一个**嵌入式关系型数据库**——它不是一个"服务器"，而是一个**库**（类似标准库），数据存在一个 `.db` 文件里。零安装、零配置，自带完整 SQL 能力。你的 `database.db` 就是这么个文件。

### 3.2 你的表设计

```sql
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- 自增主键，天然排序
    session_id TEXT NOT NULL,               -- 哪场会话
    role TEXT NOT NULL,                     -- user / assistant / tool
    content TEXT NOT NULL,                  -- 消息内容
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

**为什么这么设计**：
- `id` 自增主键：每行唯一编号，**也是时间顺序的天然依据**（后面排序用）
- `session_id` + 查询条件：按会话捞历史
- `role`：区分角色，拼 `messages` 时要用
- `AUTOINCREMENT`：保证 id 单调递增不回退

---

## 4. 三个必须掌握的 SQLite 底层点

### 4.1 参数化查询：为什么必须用 `?`（防注入）

```python
# ✅ 你写的：参数化查询
conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
             (session_id, role, content))

# ❌ 危险的：字符串拼接（SQL 注入！）
conn.execute(f"INSERT INTO messages VALUES ('{session_id}', '{content}')")
```

**原理**：参数化查询让数据库把 `?` 占位符当**数据**处理，而不是当 SQL 代码。如果用户消息里有 `'); DROP TABLE messages;--` 这种内容，拼接方式会被当成 SQL 执行（删库跑路！），参数化方式则安全地存成普通文本。

**铁律**：**永远用参数化查询，永远不要拼接 SQL。** LLM 应用的输入是模型生成的，尤其不可信，这条铁律价值加倍。

### 4.2 row_factory：让结果"能按列名访问"

```python
conn.row_factory = sqlite3.Row   # 让查询结果支持 row["role"] 访问
```

默认情况下 SQLite 返回的是**元组（tuple）**，只能 `row[0]`、`row[1]` 按位置取，不知道哪列是啥。`sqlite3.Row` 让它变成**可按列名访问**的对象，代码可读性大增。而 `load_history` 里 `[dict(row) for row in ...]` 再转成 dict，是为了直接拼给 `messages`（dict 是 LangChain/OpenAI 消息的标准格式）。

### 4.3 连接管理：为什么"每次都要开连接、用完必须关"

```python
def save_message(session_id, role, content):
    conn = get_db_connection()
    try:
        conn.execute("INSERT ...", (session_id, role, content))
        conn.commit()          # 提交事务，写入落盘
    finally:
        conn.close()          # 必须关！否则连接泄漏

def load_history(session_id):
    conn = get_db_connection()
    try:
        cursor = conn.execute("SELECT ...")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
```

几个容易忽略但关键的细节：

1. **`try/finally` 保证无论成功失败都关连接**——SQLite 连接是文件句柄，不关会泄漏；多个连接同时写还可能 `database is locked`。
2. **`commit()` 什么时候调**：写操作（INSERT）必须 `commit()` 才真正落盘；读操作（SELECT）不需要。
3. **`with conn:` 的陷阱**（你踩过的坑）：`with sqlite3.connect(...) as conn` **只管理事务（自动 commit/rollback），不会自动关闭连接**——所以标准写法是 `get → try → finally close`，而不是依赖 `with`。

> **对照你的项目**：这是你写第 3 版才改对的。你最初 `raw_factory`（应为 `row_factory`）、`with conn` 不关连接、`ORDER BY created_at`（秒级精度同秒会乱序）——现在都改对了。**记住你踩过这几个坑的"为什么"**：拼写错误不报错但行为异常、连接不关会累积、时间戳精度不够会乱序。

---

## 5. 历史注入：把记忆拼回 messages

### 你的 run_agent（阶段 2 版）

```python
def run_agent(user_message, session_id="default_session", max_iterations=5):
    history = load_history(session_id)          # 1. 从数据库捞历史
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        *history,                                # 2. 历史拼进来（关键！）
        {"role": "user", "content": user_message}
    ]
    ...
```

**这就是"记忆"的全部秘密**：`*history` 把数据库里这条 `session_id` 的历史消息展开，塞进 `messages`。LLM 一看，哦，之前聊过这些——"记得"了。

### 保存的时机（容易漏）

```python
if not response.tool_calls:   # 得到最终答案
    save_message(session_id, "user", user_message)              # 存用户消息
    save_message(session_id, "assistant", response.content)     # 存助手回答
    return response.content
```

**注意两个坑**（你之前踩过）：
1. **用户消息和助手回答都要存**——只存一个，历史就不完整（下次只记得用户说了啥，不记得你怎么回的）。
2. **兜底路径也要存**（达到 `max_iterations` 时）——否则那次对话"凭空消失"。
3. **工具过程消息（role=tool）要不要存？**——你的选择是不存（只存 user/assistant），因为工具过程是"执行细节"，用户视角只需要最终一问一答。这是一个合理的取舍，想清楚为什么即可。

---

## 6. 进阶铺垫：上下文管理（阶段 2 计划的另一半）

现在你"无脑"把所有历史都塞进 `messages`，但历史会越来越长，迟早撞上两个墙：

| 墙 | 问题 | 解法（阶段 2 计划里提了） |
|---|---|---|
| **Token 预算** | 模型上下文窗口有限，历史太长直接报错 | 按窗口大小截断最旧消息 |
| **注意力稀释** | 历史太长，模型"记不住"最相关的早期信息 | 滚动摘要：把老对话压缩成一句总结 |
| **成本** | 每次请求的 token 都算钱 | 只保留最近 N 轮 |

这三件事（**预算 / 截断 / 摘要**）是"记忆系统"的进阶内容，你的阶段 2 已经打下了存储基础，后面随时可以加上。

---

## 7. 测试：conftest 里的 tmp_db 是什么魔法

```python
# test/conftest.py（示意）
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
```

- `tmp_path`：pytest 自带 fixture，每次测试给一个**临时目录**，测完自动清理。
- `monkeypatch.setattr(db, "DB_PATH", ...)`：**运行时替换** `db.DB_PATH`，让测试用临时库而不是你真实的 `database.db`。
- **为什么必须这样**：如果测试写真实库，测试跑完真实数据库里全是垃圾数据（你之前就遇到 `test_save_message` 断言失败，因为库里混入了真实请求的"请计算 1+1"）。**测试隔离**是工程化的基本素养。

---

## 8. 常见坑 & 自测

### 常见坑
1. 忘存 assistant 回答 → 历史只有用户的话，LLM 接不上话。
2. 字符串拼接 SQL → SQL 注入风险（LLM 输入尤其危险）。
3. `with conn:` 不关连接 → 连接泄漏、`database is locked`。
4. `ORDER BY created_at` → 同秒插入乱序；用 `ORDER BY id`。
5. 测试碰真实库 → 数据污染、断言失败；用 tmp_db 隔离。
6. 不同 session 串数据 → 查询忘了 `WHERE session_id = ?`。

### 自测
1. 用自己的话解释：Agent 的"记忆"到底存在哪？请求时怎么"想起来"？
2. 为什么 `session_id` 必须由客户端带上？服务端能自己猜吗？
3. 字符串拼接 SQL 为什么危险？`?` 占位符是怎么防住的？
4. `commit()` 和 `close()` 的区别是什么？只 commit 不 close 会怎样？
5. 为什么排序用 `id` 而不是 `created_at`？
6. `row_factory = sqlite3.Row` 解决了什么问题？`[dict(row) ...]` 又是为了什么？
7. 如果消息历史要限制"只保留最近 10 轮"，`load_history` 该怎么改（SQL 层面）？

---

**下一步**：V2 能让 Agent"记住对话"了，但每轮还是靠你手写 `for` 循环管理状态——阶段 3 把它迁移到 LangGraph 状态图，让框架接管状态、循环、断点恢复。见 `阶段3_LangGraph重构与Checkpoint.md`。
