"""阶段 4 · 场景验证：Agent 组合原子工具完成「读文件 → 修改 → 写回」。

用 mock LLM 控制编排（离线、可复现，不花 token），演示：
  LLM 第 1 轮决策 → 调 read_file
  LLM 第 2 轮决策 → 调 write_file（基于第 1 轮结果）
  LLM 第 3 轮决策 → 直接回答

验证三件事：
  1. README_副本.md 真实生成
  2. 内容 = README.md 原文 + 追加行
  3. ToolPipeline 审计日志有 read_file / write_file 的 [Before]/[After]
"""

import sys
import io
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.graph_agent import build_graph, pipeline

# ---------------------------------------------------------------------------
# ① 先真实读一次 README，作为第 2 轮 write_file 的内容素材
#    （模拟真实场景：LLM 是基于第 1 轮 read_file 的结果来生成写入内容的）
# ---------------------------------------------------------------------------
original = Path("README.md").read_text(encoding="utf-8")
appended = original + "\n\n（由 demo_scene.py 追加的一行）"

# ---------------------------------------------------------------------------
# ② LLM 三轮"剧本"：读 → 写 → 收尾
# ---------------------------------------------------------------------------
calls = [
    # 第 1 轮：请求 read_file
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"path": "README.md"},
                "id": "c1",
                "type": "tool_call",
            }
        ],
    ),
    # 第 2 轮：请求 write_file（内容 = 原文 + 追加行）
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"path": "README_副本.md", "content": appended},
                "id": "c2",
                "type": "tool_call",
            }
        ],
    ),
    # 第 3 轮：直接回答
    AIMessage(content="已完成，副本已生成"),
]

SYSTEM = SystemMessage(content="You are a helpful assistant.")

with patch("app.graph_agent.call_llm", side_effect=calls) as mocked:
    graph = build_graph()
    result = graph.invoke(
        {
            "messages": [
                SYSTEM,
                HumanMessage(content="把 README.md 复制并追加一行，存成副本 README_副本.md"),
            ],
            "session_id": "scene",
        },
    )

# ---------------------------------------------------------------------------
# ③ 验证三件事
# ---------------------------------------------------------------------------
print("=== 1) LLM 决策轮数 ===")
print(f"call_count: {mocked.call_count}（期望 3：读 → 写 → 收尾）")

print("\n=== 2) 副本文件 ===")
copy = Path("README_副本.md")
print(f"文件存在: {copy.exists()}")
if copy.exists():
    content = copy.read_text(encoding="utf-8")
    print(f"内容 = 原文 + 追加行: {content == appended}")
    print(
        f"副本行数: {len(content.splitlines())} | 原文行数: {len(original.splitlines())}"
    )

print("\n=== 3) ToolPipeline 审计日志 ===")
pipeline.show_logs()

print("\n=== 最终回答 ===")
print(result["messages"][-1].content)
