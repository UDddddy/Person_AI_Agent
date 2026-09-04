from tools.registry import Tools
from app.event_bus import EVENT_TOOL_CALL, EVENT_TOOL_RESULT
import time

class ToolPipeline:

    def __init__(self, before_hook=None, after_hook=None, bus=None):
        self.logs = []          # 审计日志列表
        self.before_hook = before_hook
        self.after_hook = after_hook
        self.bus = bus          # 可选事件总线：None 时跳过 emit，向后兼容

    def execute(self, name: str, args: dict) -> str:
        # Prepare：查工具 → 校验必填参数 → before_hook 拦截检查
        self.start_time = time.perf_counter()
        if name not in Tools:
            return "工具不存在"
        tool = Tools[name]
        if not all(arg in args for arg in tool.parameters["required"]):
            return "参数不完整"
        if self.before_hook:
            denied = self.before_hook(name, args)   # ① 接住返回值
            if denied:                              # ② 非 None = 要拦截
                self.logs.append(f"[Blocked] {name}({args}) -> {denied}")
                return denied                       # ③ 返回拒绝原因，不再执行工具
        self.logs.append(f"[Before] {name}({args})")
        if self.bus:  # Execute 前发 tool_call 事件
            self.bus.emit(EVENT_TOOL_CALL, {"name": name, "args": args})
        result = tool.execute(args)
        if self.bus:  # Execute 后发 tool_result 事件
            self.bus.emit(EVENT_TOOL_RESULT, {"name": name, "result": result})
        self.logs.append(f"[After] {name}({args}) -> {result},time={time.perf_counter()-self.start_time}")
        if self.after_hook:
            self.after_hook(name, args, result, time.perf_counter()-self.start_time)
        return str(result)


    def show_logs(self):
        """打印审计日志（调试用）"""
        for log in self.logs:
            print(log)