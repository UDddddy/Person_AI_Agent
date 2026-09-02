"""阶段 3 联调脚本：验证 SqliteSaver Checkpoint 的多轮记忆与重启恢复。"""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from langgraph.checkpoint.sqlite import SqliteSaver

from app.graph_agent import build_graph, run_graph_agent


def main():
    checkpoint_path = "app/checkpoints.sqlite"

    # ---- 第一轮：新会话，写入历史 ----
    with SqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        print("=" * 50)
        print("第一轮：让 AI 记住一个事实（学习计划第 3 阶段 = LangGraph）")
        r1 = run_graph_agent(
            "记住：我们项目的下一个阶段是 LangGraph 重构。",
            session_id="sess-checkpoint",
            checkpointer=checkpointer,
        )
        print("回答:", r1)

    # ---- 第二轮：同一会话，验证能否想起上轮内容（模拟“重启”后） ----
    with SqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        print("=" * 50)
        print("第二轮（重启服务后，同一 session_id）：我们的下一个阶段是什么？")
        r2 = run_graph_agent(
            "我们项目的下一个阶段是什么？",
            session_id="sess-checkpoint",
            checkpointer=checkpointer,
        )
        print("回答:", r2)

    # ---- 第三轮：换一个新 session_id，验证会话隔离 ----
    with SqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        print("=" * 50)
        print("第三轮（新 session_id，应当不记得上面的事实）：我上次告诉过你什么？")
        r3 = run_graph_agent(
            "我上次告诉过你什么？",
            session_id="sess-other",
            checkpointer=checkpointer,
        )
        print("回答:", r3)


if __name__ == "__main__":
    main()
