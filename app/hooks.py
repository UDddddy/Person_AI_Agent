class HookPoint:
    # Session 层
    SESSION_START = "session.start"
    # AgentLoop 层
    TURN_START = "agent.turn_start"
    MESSAGE_CHUNK = "agent.message_chunk"
    TURN_END = "agent.turn_end"
    # Tool 层
    BEFORE_TOOL = "tool.before"     # 拦截型
    AFTER_TOOL = "tool.after"
    TOOL_ERROR = "tool.error"
    # Provider 层
    BEFORE_LLM = "llm.before"       # 拦截型（阶段7用）
    AFTER_LLM = "llm.after"


class HookRegistry:
    def __init__(self):
        self._hooks = {}            # {点位: [钩子函数, ...]}，和 EventBus 的账本结构一样

    def register(self, point, fn):
        # 没键先建 []，再 append（同 subscribe，支持多点位多钩子）
        if point not in self._hooks:
            self._hooks[point] = []
        self._hooks[point].append(fn)

    def emit(self, point, **kwargs):
        """通知型：依次调用，忽略返回值——对照 EventBus.emit 写，几乎一样"""
        for fn in self._hooks.get(point, []):
            fn(**kwargs)

    def intercept(self, point, **kwargs):
        """拦截型：按注册顺序逐个调用，【接住返回值】，
        第一个返回非 None 的钩子立即 return 它的结果（短路）；
        全部返回 None 则 return None（放行）。"""
        for fn in self._hooks.get(point, []):
            ret = fn(**kwargs)
            if ret is not None:
                return ret
        return None
