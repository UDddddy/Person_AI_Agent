"""阶段 3 联调脚本：用真实 LLM 验证 LangGraph Agent（绕开终端中文乱码）。"""

import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.graph_agent import run_graph_agent


def main():
    print("=" * 50)
    print("测试 1：无工具直接问答")
    r1 = run_graph_agent("你好，请简单介绍一下你自己")
    print("回答:", r1)

    print("=" * 50)
    print("测试 2：调用 calculator 工具")
    r2 = run_graph_agent("请帮我计算 1+1 等于多少？", session_id="test_tool_calc")
    print("回答:", r2)

    print("=" * 50)
    print("测试 3：调用 get_current_time 工具")
    r3 = run_graph_agent("现在几点？", session_id="test_tool_time")
    print("回答:", r3)


if __name__ == "__main__":
    main()
