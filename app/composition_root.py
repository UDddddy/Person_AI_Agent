from app.extensions.manager import ExtensionManager
from app.extensions.observability import ObservabilityExtension
from app.extensions.approval import ApprovalExtension
from app.extensions.context import ExtensionContext
from app.event_bus import EventBus
from app.hooks import HookRegistry
from tools.pipeline import ToolPipeline


def create_pipeline(include_approval: bool = True) -> ToolPipeline:
    """装配工具执行管道：创建总线/钩子，注册扩展并两阶段绑定。

    Args:
        include_approval: 是否注册 approval 审批扩展。
            - True（默认）：主链路用。危险工具经 BEFORE_TOOL 拦截钩子，
              默认 approver=None 时一律自动拒绝（无人值守安全优先）。
            - False：HITL 图（graph_agent_hitl.py）用。人工审批由 LangGraph
              interrupt 完成，若再挂 approval 扩展，钩子会先把危险工具拦下、
              interrupt 永远收不到审批请求——两条审批路径互斥，只能选一条。

    Returns:
        装配好的 ToolPipeline（bus/hooks 已注入）。
    """
    bus = EventBus()
    hooks = HookRegistry()
    manager = ExtensionManager()
    manager.register(ObservabilityExtension())
    if include_approval:
        manager.register(ApprovalExtension())
    ctx = ExtensionContext(bus=bus, hooks=hooks)
    manager.bind_all(ctx)                       # 两阶段绑定：load + setup
    return ToolPipeline(bus=bus, hooks=hooks)   # 把装配好的总线/钩子交回管道
