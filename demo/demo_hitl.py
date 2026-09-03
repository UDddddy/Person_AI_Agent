"""HITL 人工审批命令行演示（阶段 3）。

真 LLM + input() 交互：
- 问一个会触发 run_command 的问题（如"帮我跑一下 ls"），
- 图会在执行危险工具前暂停，弹出一个审批请求，
- 你输入 approve / reject 决定是否放行。

运行：.venv\\Scripts\\python.exe -m demo.demo_hitl
"""

from app.graph_agent_hitl import run_hitl


def main():
    print("=" * 50)
    print(" HITL 人工审批演示（危险工具需审批）")
    print("=" * 50)
    print("试试问：'帮我跑一下 ls' 或 '删除整个文件夹'")
    print("图会在执行危险工具前暂停，等你输入 approve / reject")
    print("输入 exit 退出\n")

    session_id = "demo_hitl"
    while True:
        user_input = input("你: ").strip()
        if user_input in ("exit", "quit", "退出"):
            break
        if not user_input:
            continue

        answer = run_hitl(user_input, session_id=session_id)
        print(f"\nAgent: {answer}\n")


if __name__ == "__main__":
    main()
