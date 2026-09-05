"""阶段 5 · 课5：approval 审批扩展（拦截型扩展示范）。

observability 用 ctx.bus 做"事后通知"；approval 正相反，它用 ctx.hooks 在
Tool.before 点位注册【拦截】钩子：危险工具必须经 approver 批准，未批准就返回
拒绝原因、工具本体不会执行。
"""

from .base import BaseExtension
from app.hooks import HookPoint


class ApprovalExtension(BaseExtension):
    name = "approval"

    def __init__(self, dangerous_tools=None, approver=None):
        # 危险工具名单，默认 run_command
        self.dangerous_tools = list(dangerous_tools) if dangerous_tools else ["run_command"]
        # 审批决策函数 approver(name, args) -> bool：True=批准放行，False=拒绝。
        # 默认 None = 安全优先：危险工具一律自动拒绝（无人值守也不会误执行）。
        self.approver = approver

    def _check_before_tool(self, name, args):
        """挂到 BEFORE_TOOL 的拦截钩子：返回 None 放行，返回字符串=拦截原因。"""
        if name not in self.dangerous_tools:
            return None                                   # 非危险工具，不干预
        approved = self.approver(name, args) if self.approver else False
        if approved:
            return None                                   # 审批通过 → 放行
        return f"工具 {name} 属于危险操作，审批未通过，已拦截（参数 {args}）"

    def setup(self, ctx):
        """绑定阶段：approval 需要拦截能力，所以取 ctx.hooks（不是 ctx.bus）。"""
        if ctx.hooks is None:
            return
        ctx.hooks.register(HookPoint.BEFORE_TOOL, self._check_before_tool)
