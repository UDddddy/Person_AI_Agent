"""阶段 5 · 课5验证：approval 审批扩展 与 observability 协作。

一个核心管道（ToolPipeline）+ 两个扩展：
- observability 用 ctx.bus 做"通知"（结构化 JSON 日志）；
- approval 用 ctx.hooks 做"拦截"（危险工具审批）。

从项目根运行：python -m demo.demo_approval
"""

from app.event_bus import EventBus
from app.hooks import HookRegistry
from app.extensions.context import ExtensionContext
from app.extensions.manager import ExtensionManager
from app.extensions.observability import ObservabilityExtension
from app.extensions.approval import ApprovalExtension
from tools.pipeline import ToolPipeline


def main():
    # 1) 两套运行时机制：总线（通知）+ 钩子注册表（拦截）
    bus = EventBus()
    hooks = HookRegistry()
    ctx = ExtensionContext(bus=bus, hooks=hooks)

    # 2) 注册两个扩展并一次性绑定（各自从 ctx 取自己需要的能力）
    manager = ExtensionManager()
    manager.register(ObservabilityExtension())
    manager.register(ApprovalExtension(dangerous_tools=["run_command"]))  # 默认自动拒绝
    manager.bind_all(ctx)

    # 3) 管道同时接上总线与钩子
    pipeline = ToolPipeline(bus=bus, hooks=hooks)

    print("=== ① 普通工具 calculator：approval 放行，observability 打两条日志 ===")
    print("结果:", pipeline.execute("calculator", {"expression": "3*4"}))

    print("\n=== ② 危险工具 run_command：approval 拦截（无 tool_call 日志、工具不执行）===")
    print("结果:", pipeline.execute("run_command", {"command": "dir"}))

    print("\n=== ③ 审计日志：calculator 走 [Before]/[After]，run_command 只走 [Blocked] ===")
    pipeline.show_logs()


if __name__ == "__main__":
    main()
