"""阶段 5 · 课4验证：分层 Hooks —— Tool 层 before 拦截链。

演示要点：
1. 同一个点位 BEFORE_TOOL 可挂【多个】钩子，注册顺序 = 执行顺序；
2. 钩子返回 None = 放行，返回字符串 = 拦截原因并【短路】（后续钩子/工具都不执行）；
3. 拦截路径记 [Blocked]，放行路径记 [Before]/[After]。

从项目根运行：python -m demo.demo_hooks
"""

from app.hooks import HookRegistry, HookPoint
from tools.pipeline import ToolPipeline


def observe_before(name, args):
    """钩子①：只观察、不拦截（返回 None = 放行）。"""
    print(f"[钩子①·观察] 即将执行工具 {name}，参数 {args}")
    return None


def block_dangerous(name, args):
    """钩子②：危险工具拦截（返回字符串 = 拒绝原因，触发短路）。"""
    if name == "run_command":
        return f"危险工具 {name} 已被审批钩子拦截"
    return None


def main():
    # 1) 建钩子注册表，给 Tool.before 点位挂两个钩子
    hooks = HookRegistry()
    hooks.register(HookPoint.BEFORE_TOOL, observe_before)
    hooks.register(HookPoint.BEFORE_TOOL, block_dangerous)

    # 2) 把注册表注入管道（故意不传旧 before_hook，专门验证新机制独立生效）
    pipeline = ToolPipeline(hooks=hooks)

    print("=== ① calculator：钩子①观察、钩子②放行，应正常算出结果 ===")
    print("结果:", pipeline.execute("calculator", {"expression": "2*3"}))

    print("\n=== ② run_command：钩子②拦截，工具本体不应执行（无'模拟命令执行成功'）===")
    print("结果:", pipeline.execute("run_command", {"command": "dir"}))

    print("\n=== ③ 审计日志：calculator 走 [Before]/[After]，run_command 只走 [Blocked] ===")
    pipeline.show_logs()


if __name__ == "__main__":
    main()
