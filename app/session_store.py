"""阶段6 · 课1：树形会话存储（append-only，SQLite 实现 JSONL 结构）。

每条消息是一个节点（Entry），带 parent_id 指向上一条；只追加不修改。
核心能力：
- append_entry：追加节点，返回含 id/timestamp 的完整 dict
- get_entry：按 id 查单个节点
- get_leaf：取会话当前叶子（按时间最新，课3会细化分支概念）
- _row_to_dict：读库时把 tool_calls / metadata 的 JSON 字符串解析回对象

设计要点：
- append-only：永远不 UPDATE/DELETE，崩溃安全、天然支持分支回溯
- parent_id 为 None 的节点是根节点（一条会话链的起点）
- tool_calls / metadata 存 JSON 字符串，读写时自动序列化/反序列化
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from app.compaction import (
    chain_tokens,
    find_safe_cut_point,
    should_compact,
)

# 独立数据库文件，和阶段2的 messages 表（database.db）、阶段3的 Checkpoint（checkpoints.sqlite）隔离
DB_PATH = Path(__file__).resolve().parent / "sessions.db"


def _get_conn():
    """获取 SQLite 连接，row_factory 设为 Row 方便按列名取值。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_store():
    """建表 + 建索引。幂等，可重复调用。"""
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_entries (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_id TEXT,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_call_id TEXT,
                metadata TEXT
            )
            """
        )
        # 按会话查所有节点、按父节点查子节点（分支/回溯高频路径）
        conn.execute("CREATE INDEX IF NOT EXISTS idx_se_session ON session_entries(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_se_parent ON session_entries(parent_id)")
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row):
    """把 SQLite Row 转成 dict，并把 JSON 字符串字段解析回 Python 对象。"""
    d = dict(row)
    if d.get("tool_calls"):
        try:
            d["tool_calls"] = json.loads(d["tool_calls"])
        except (json.JSONDecodeError, TypeError):
            pass  # 解析失败保留原字符串，不崩
    if d.get("metadata"):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def append_entry(
    session_id,
    parent_id,
    entry_type,
    content,
    tool_calls=None,
    tool_call_id=None,
    metadata=None,
):
    """追加一个节点（append-only），返回完整 entry dict（含生成的 id/timestamp）。

    参数：
    - session_id：会话标识
    - parent_id：父节点 id，根节点传 None
    - entry_type：user / assistant / tool / system / compaction
    - content：消息文本
    - tool_calls：assistant 类型的工具调用列表（dict/list，自动 json.dumps）
    - tool_call_id：tool 类型对应的调用 id（配对用）
    - metadata：额外信息 dict（如 token_count，自动 json.dumps）
    """
    entry_id = uuid.uuid4().hex
    timestamp = datetime.now().isoformat()
    tool_calls_json = (
        json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
    )
    metadata_json = (
        json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    )

    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO session_entries
                (id, session_id, parent_id, timestamp, type, content,
                 tool_calls, tool_call_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                session_id,
                parent_id,
                timestamp,
                entry_type,
                content,
                tool_calls_json,
                tool_call_id,
                metadata_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": entry_id,
        "session_id": session_id,
        "parent_id": parent_id,
        "timestamp": timestamp,
        "type": entry_type,
        "content": content,
        "tool_calls": tool_calls,
        "tool_call_id": tool_call_id,
        "metadata": metadata,
    }


def get_entry(entry_id):
    """按 id 查单个节点，不存在返回 None。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM session_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_leaf(session_id):
    """取会话当前叶子：同 session 下按 timestamp DESC 取第一条。

    初期用"时间最新"定义叶子；课3引入分支后，会细化为"当前活跃分支的叶子"。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM session_entries WHERE session_id = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def build_chain(session_id, leaf_id=None):
    """从叶子沿 parent_id 回溯到根，重建消息链（返回从根到叶子的顺序）。

    - leaf_id 为 None 时自动取 get_leaf(session_id)
    - 回溯过程中如果某个 parent_id 找不到（数据损坏/跨会话），停止回溯
    - 返回 list[dict]，顺序 [根节点, ..., 叶子节点]
    - 空会话返回 []
    """
    if leaf_id is None:
        leaf = get_leaf(session_id)
        if leaf is None:
            return []
        leaf_id = leaf["id"]

    chain = []
    current_id = leaf_id
    while current_id is not None:
        entry = get_entry(current_id)
        if entry is None:
            break  # 父节点不存在，停止回溯（不崩，返回已重建的部分）
        chain.append(entry)
        current_id = entry["parent_id"]

    chain.reverse()  # 回溯是 叶子→根，反转成 根→叶子
    return chain


def get_children(parent_id):
    """获取某个节点的所有直接子节点（按 timestamp 升序）。

    Fork/时间旅行后，一个节点可能有多个子节点，用这个函数查看分支情况。
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM session_entries WHERE parent_id = ? ORDER BY timestamp ASC",
            (parent_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def fork_session(from_entry_id, new_session_id):
    """从 from_entry 节点 Fork 出新会话 new_session_id。

    创建一个 type='fork' 的标记节点作为新会话起点，parent_id 指向 from_entry。
    之后 build_chain(new_session_id) 会自动回溯到原会话的完整历史。
    原会话不受影响（append-only，新会话是独立分支）。
    """
    origin = get_entry(from_entry_id)
    if origin is None:
        raise ValueError(f"Fork 起点不存在: {from_entry_id}")
    return append_entry(
        session_id=new_session_id,
        parent_id=from_entry_id,
        entry_type="fork",
        content=f"Forked from session {origin['session_id']} at {from_entry_id[:8]}",
        metadata={"forked_from": from_entry_id, "origin_session": origin["session_id"]},
    )


def travel_to(session_id, entry_id):
    """时间旅行：验证 entry_id 属于该会话，返回它作为新的 append 起点。

    之后调用 append_entry(session_id, parent_id=entry_id, ...) 就会从历史节点
    长出新分支。原后续节点仍在库中（append-only 永不删除），但 get_leaf / build_chain
    会走新分支——这就是"回到过去重新开始"。
    """
    entry = get_entry(entry_id)
    if entry is None:
        raise ValueError(f"节点不存在: {entry_id}")
    if entry["session_id"] != session_id:
        raise ValueError(f"节点 {entry_id} 不属于会话 {session_id}（属于 {entry['session_id']}）")
    return entry


def compact_session(session_id, max_tokens, summary_text, force=False):
    """压缩会话：超过阈值时，把链前部替换为摘要节点，保留部分重放到摘要后。

    append-only 下的压缩策略（不删不改旧节点）：
    1. build_chain 取当前链
    2. find_safe_cut_point 找安全切割点（工具调用/结果配对完整）
    3. 追加摘要节点作为新根（parent_id=None）
    4. 把保留部分（chain[cut:]）重新追加，parent 链指向摘要
    5. 旧节点仍在库中（可审计），当前链从摘要开始

    force=True 时跳过阈值检查（手动 /compact 用）；否则超 max_tokens 才压缩。
    返回压缩后的新链 [摘要节点, ...重放的保留部分]。
    不需要压缩时原样返回当前链。
    """
    chain = build_chain(session_id)
    if not force and not should_compact(chain, max_tokens):
        return chain

    cut = find_safe_cut_point(chain, max_tokens)

    # 1. 摘要节点作为新根（parent_id=None），metadata 记录压缩审计信息
    comp_entry = append_entry(
        session_id=session_id,
        parent_id=None,
        entry_type="compaction",
        content=summary_text,
        metadata={
            "compressed_count": cut,
            "original_chain_tokens": chain_tokens(chain),
            "original_leaf": chain[-1]["id"] if chain else None,
        },
    )

    # 2. 重放保留部分：逐条 append，parent 链指向摘要
    parent_id = comp_entry["id"]
    replayed = []
    for entry in chain[cut:]:
        new_entry = append_entry(
            session_id=session_id,
            parent_id=parent_id,
            entry_type=entry["type"],
            content=entry["content"],
            tool_calls=entry.get("tool_calls"),
            tool_call_id=entry.get("tool_call_id"),
            metadata={
                **(entry.get("metadata") or {}),
                "replayed_from": entry["id"],
            },
        )
        replayed.append(new_entry)
        parent_id = new_entry["id"]

    return [comp_entry] + replayed
