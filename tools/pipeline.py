from tools.registry import Tools
from app.event_bus import EVENT_TOOL_CALL, EVENT_TOOL_RESULT
from app.hooks import HookPoint
import time

class ToolPipeline:

    def __init__(self, before_hook=None, after_hook=None, bus=None, hooks=None):
        self.logs = []          # 审计日志列表（人读的字符串行）
        self.trace = []         # 【新增】结构化执行轨迹：前端"工具调用时间线"数据源
        self.before_hook = before_hook
        self.after_hook = after_hook
        self.bus = bus          # 可选事件总线：None 时跳过 emit，向后兼容
        self.hooks = hooks      # 可选分层钩子注册表：None 时不拦截

    def execute(self, name: str, args: dict) -> str:
        # Prepare：查工具 → 校验必填参数 → before 拦截检查
        self.start_time = time.perf_counter()
        if name not in Tools:
            self.trace.append({"name": name, "args": args, "result": "工具不存在",
                               "latency": 0.0, "blocked": True})
            return "工具不存在"
        tool = Tools[name]
        if not all(arg in args for arg in tool.parameters.get("required",[])):
            self.trace.append({"name": name, "args": args, "result": "参数不完整",
                               "latency": 0.0, "blocked": True})
            return "参数不完整"
        # before 拦截：先跑分层钩子链，再兼容旧的单函数钩子
        denied = None
        if self.hooks:                                                                   # 判空
            denied = self.hooks.intercept(HookPoint.BEFORE_TOOL, name=name, args=args)  # 接住返回值
        if denied is None and self.before_hook:        # 新链放行后，再问旧的单函数钩子
            denied = self.before_hook(name, args)
        if denied:                                     # 任一来源拦截 → 统一出口
            self.logs.append(f"[Blocked] {name}({args}) -> {denied}")
            self.trace.append({"name": name, "args": args, "result": denied,
                               "latency": time.perf_counter()-self.start_time,
                               "blocked": True})
            return denied
        self.logs.append(f"[Before] {name}({args})")
        if self.bus:  # Execute 前发 tool_call 事件
            self.bus.emit(EVENT_TOOL_CALL, {"name": name, "args": args})
        result = tool.execute(args)
        latency = time.perf_counter() - self.start_time
        if self.bus:  # Execute 后发 tool_result 事件（带延迟）
            self.bus.emit(EVENT_TOOL_RESULT, {"name": name, "result": result, "latency": latency})
        self.logs.append(f"[After] {name}({args}) -> {result},time={time.perf_counter()-self.start_time}")
        # 结构化轨迹：blocked=False 表示真正执行了工具（审批拦截的不算执行）
        self.trace.append({"name": name, "args": args, "result": result,
                           "latency": latency, "blocked": False})
        if self.after_hook:
            self.after_hook(name, args, result, time.perf_counter()-self.start_time)
        return str(result)


    def show_logs(self):
        """打印审计日志（调试用）"""
        for log in self.logs:
            print(log)
