from tools.registry import Tools
import time

class ToolPipeline:

    def __init__(self, before_hook=None, after_hook=None):
        self.logs = []          # 审计日志列表
        self.before_hook = before_hook
        self.after_hook = after_hook

    def execute(self, name: str, args: dict) -> str:
        # 你的任务：按三段实现
        # Prepare: 1) 查 Tools[name]，不存在给友好错误
        #          2) 校验 args 里 required 字段是否齐全（对照 tool.schema()["required"]）
        #          3) 记录 before 日志
        # Execute: 调 Tools[name].execute(args)（execute 自带异常兜底）
        # Finalize: 1) 记录 after 日志（耗时、结果摘要）
        #           2) 返回结果字符串
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
        result = tool.execute(args)
        self.logs.append(f"[After] {name}({args}) -> {result},time={time.perf_counter()-self.start_time}")
        if self.after_hook:
            self.after_hook(name, args, result, time.perf_counter()-self.start_time)
        return str(result)


    def show_logs(self):
        """打印审计日志（调试用）"""
        for log in self.logs:
            print(log)