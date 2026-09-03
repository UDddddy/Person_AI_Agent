# Personal AI Agent 开发总结 · 阶段 2

**阶段名称**：V2 多轮会话 + SQLite 记忆
**完成时间**：2026-09-01
**Git 节点**：`3da7add` feat:阶段2 多轮对话+SQLite 记忆

---

## 一、阶段目标

解决 Agent 的**"失忆"问题**：V1 的 Agent 每次对话都是全新开始，`messages` 只有 `[system, 当前问题]`，完全不知道"之前聊过什么"。

阶段 2 要达成的两个核心能力：
1. **多轮会话**：用 `session_id` 区分不同场对话，同一场能翻看历史
2. **持久化**：把消息存进数据库，**服务重启也不丢**

## 二、完成情况

| 能力 | 验收结果 |
|---|---|
| 多轮会话 | ✅ 连续两条请求，第二条能答上"刚才的结果 +10 = 12" |
| 持久化 | ✅ 重启服务后，AI 仍记得之前对话内容 |
| 自动化测试 | ✅ `20 passed` 全绿 |

## 三、代码改动清单

| 文件 | 状态 | 作用 |
|---|---|---|
| `app/db.py` | 🆕 新建 | 存储层：连接管理、建表、`save_message` / `load_history` |
| `app/agent.py` | ✏️ 修改 | `run_agent` 加载历史注入 messages + 对话结束保存 |
| `app/schema.py` | ✏️ 修改 | `ChatRequest` 增加 `session_id` 可选字段 |
| `app/main.py` | ✏️ 修改 | startup 钩子建表；`chat_endpoint` 透传 `session_id` |
| `test/conftest.py` | 🆕 新建 | 共享 `tmp_db` fixture（测试隔离） |
| `test/test_db.py` | 🆕 新建 | 存储层 4 个测试 |
| `test/test_agent.py` | ✏️ 修改 | 补 2 个会话测试，全部测试接入临时库 |
| `test_api.py` | 🆕 新建 | 联调脚本（绕开终端中文乱码） |

## 四、关键设计决策

### 1. 数据模型：一张 `messages` 表，按 `session_id` 分组
```
messages
├── session_id="abc" ── user "请计算1+1"
│                      ├── assistant "调用 calculator"
│                      ├── tool "2"
│                      └── assistant "1+1 等于 2"
└── session_id="xyz" ── ...
```
每个 `session_id` 代表一场独立对话，靠它区分"哪场聊到哪了"。

### 2. 为什么用 SQLite
Python **内置模块**（`import sqlite3`），零安装、单文件存储，不需要装数据库服务。个人学习项目用它足够，且是通向 MySQL/PostgreSQL 的最佳跳板（SQL 语法一致）。

### 3. 只存 "user + 最终 assistant 答案"，不存 tool 过程
tool 调用/结果是**计算过程**（1+1 → 调 calculator → 得 2），下一轮只需要最终结论。存过程会让历史又长又乱（还带 `tool_call_id` 复杂结构）。

### 4. `session_id` 由客户端传
服务端生成 id 的话客户端无法复用；客户端传同一 id 才能继续同一场对话。

## 五、遇到的问题与解决（7 类）

### 1. 存储层正确性
| 问题 | 原因 | 解决 |
|---|---|---|
| `conn.raw_factory` 不生效 | 拼写错误，正确是 `row_factory`；错误属性不报错但无效 | 改正拼写 |
| 连接泄漏 | `with conn:` 只管理**事务**（commit/rollback），**不关闭连接** | 统一 `get → try 操作 → finally close` 模式 |
| 历史顺序错乱 | `CURRENT_TIMESTAMP` 精度只到秒，同秒内顺序不稳 | 改按自增主键 `ORDER BY id ASC` |
| `load_history` 返回类型 | 返回 `sqlite3.Row` 对象，不是 dict，无法直接拼给 messages | `[dict(row) for row in ...]` |

### 2. Agent 逻辑错误
- **tool 循环里误存**：把每次 tool 执行结果（如 `"2"`）存成了 `assistant` 消息 → 历史混入"假回答"，污染上下文。**应删掉，只存最终答案。**
- **正常返回点漏存**：只存了 `user` 没存 `assistant` → 绝大多数正常对话的历史断裂。两个返回点（正常 + 兜底）都要存完整一对。

### 3. 测试隔离
- 现有测试 `run_agent` 会真实读写 `app/database.db` → 污染数据、结果不稳定
- 解决：`tmp_path` + `monkeypatch` 把 `DB_PATH` 指向临时文件；测试函数必须**显式接收 `tmp_db` 参数**才生效
- 教训：**测试必须自包含**——先造数据再断言，不能依赖外部遗留数据

### 4. 数据库路径
`DB_PATH = "app/database.db"` 是相对路径，启动目录不对就会建错位置。建议改用 `Path(__file__).resolve().parent` 的绝对路径（本次保留相对路径，约定从项目根目录启动）。

### 5. 终端中文乱码
Windows 控制台默认 GBK，Python 输出 UTF-8 → 乱码。绕开方案：**用 Python 脚本（httpx）发请求**，内部全 UTF-8，无编码坑。

### 6. Python 环境不一致
`python` 命令与 `pip` 指向不同环境（pip 在 Anaconda），`python -c "import yaml"` 失败。教训：确认命令实际指向的环境，用对的解释器。

### 7. Git 远程推送
首次 `git push -u origin main` 报 `src refspec main does not match any`——本地分支叫 `master`，GitHub 默认分支叫 `main`。两种解法：重命名分支或直接推 `master`。

## 六、本阶段掌握的核心概念

1. **参数化查询防 SQL 注入**：`?` 占位传元组，绝不拼接字符串
2. **连接管理心智模型**：`with conn:` ≠ 关闭连接；`try/finally close` 才安全
3. **列表展开 `*history`**：`[system, *history, user]` 把历史铺进 messages
4. **会话模型**：`session_id` 是"这场对话是谁"
5. **持久化 vs 内存**：SQLite 存文件，进程重启数据还在
6. **测试隔离哲学**：fixture + 临时库，测试不碰真实数据

## 七、下一步规划

将持久化从 SQLite 升级为 **MySQL（主存储）+ Redis（缓存/会话）**，已加入开发计划（见《Personal_AI_Agent项目计划_重构版》新增章节）。
