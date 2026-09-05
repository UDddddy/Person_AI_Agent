"""阶段6 · 课4：Compaction 上下文压缩（基础版）。

核心规则：
1. Token 估算：零新依赖，中文1字≈1.5 token，英文/数字4字符≈1 token
2. 安全切割点：工具调用（assistant 带 tool_calls）和工具结果（tool）必须配对，
   不能在"调用了但还没结果"的位置切割
3. 压缩：切割点之前的消息替换为一条 compaction 摘要，原节点不删（append-only）

触发机制（手动/阈值/溢出重试）在课5实现，本课只做"给定链和预算，怎么安全压缩"。
"""

import json


def estimate_tokens(text):
    """简单 token 估算（零依赖）。

    中文字符按 1.5 token/字，其余字符按 0.25 token/字符（≈英文4字符1 token）。
    学习项目用估算，不引入 tiktoken；生产环境应替换为真实 tokenizer。
    """
    if not text:
        return 0
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cn
    return int(cn * 1.5 + other * 0.25)


def entry_tokens(entry):
    """估算一个 entry 的 token 数：content + tool_calls 序列化后。"""
    total = estimate_tokens(entry.get("content") or "")
    if entry.get("tool_calls"):
        total += estimate_tokens(json.dumps(entry["tool_calls"], ensure_ascii=False))
    return total


def find_safe_cut_point(chain, max_tokens):
    """找安全切割点：不超过 max_tokens 且所有工具调用都已配对的位置。

    返回切割索引 cut：chain[:cut] 会被压缩，chain[cut:] 保留。
    - 整条链不超预算 → 返回 0（不需要压缩）
    - 超过预算 → 返回最后一个"待配对工具调用为空"的位置
    - 极端情况（第一个消息就超预算且无安全点）→ 返回 1（至少保留第一条）

    配对逻辑：
    - 遇到 assistant 带 tool_calls → 把所有 tool_call_id 加入待配对集合
    - 遇到 tool 消息 → 从待配对集合移除对应的 tool_call_id
    - 待配对集合为空 = 当前位置安全（所有调用都有结果了）
    """
    cumulative = 0
    pending = set()  # 待配对的 tool_call_id
    last_safe = 0    # 最后一个安全位置（索引，含义：chain[:last_safe] 可压缩）

    for i, entry in enumerate(chain):
        cumulative += entry_tokens(entry)

        # 更新待配对集合
        if entry["type"] == "assistant" and entry.get("tool_calls"):
            for tc in entry["tool_calls"]:
                if tc.get("id"):
                    pending.add(tc["id"])
        elif entry["type"] == "tool" and entry.get("tool_call_id"):
            pending.discard(entry["tool_call_id"])

        if cumulative > max_tokens:
            # 超过预算，返回最后一个安全点；如果连第一条都不安全，至少切1
            return max(last_safe, 1)

        if not pending:
            last_safe = i + 1  # 安全位置 = 当前消息之后

    return 0  # 整条链不超预算，不需要压缩


def compact_chain(chain, max_tokens, summary_text):
    """压缩链：切割点之前替换为摘要消息，之后保留。返回新链（不修改原链）。

    - 不需要压缩时（cut=0）原样返回
    - 压缩后的第一条是 type='compaction' 的摘要节点（id 等字段由调用方 append 时填充）
    """
    cut = find_safe_cut_point(chain, max_tokens)
    if cut == 0:
        return list(chain)

    summary_entry = {
        "id": None,
        "session_id": None,
        "parent_id": None,
        "timestamp": None,
        "type": "compaction",
        "content": summary_text,
        "tool_calls": None,
        "tool_call_id": None,
        "metadata": {"compressed_count": cut, "cut_index": cut},
    }
    return [summary_entry] + chain[cut:]


def chain_tokens(chain):
    """计算整条链的 token 估算值。"""
    return sum(entry_tokens(e) for e in chain)


def should_compact(chain, max_tokens):
    """是否需要压缩：链总 token 超过阈值。"""
    return chain_tokens(chain) > max_tokens
