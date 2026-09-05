"""阶段 5 · 课5：扩展运行时上下文（ExtensionContext）。

绑定阶段（bind_all）由外部把运行时能力组装好，通过 ctx 注入给每个扩展。
扩展只有在 setup(ctx) 时才拿得到这些能力；加载阶段（register）接触不到，
这就是两阶段隔离。

以后新增能力（如 send_message / set_model）只需往这里加字段，
所有扩展的 setup 签名保持不变（对扩展开放、对修改封闭）。
"""


class ExtensionContext:
    def __init__(self, bus=None, hooks=None):
        self.bus = bus        # EventBus：通知型能力（订阅/发射事件，返回值被忽略）
        self.hooks = hooks    # HookRegistry：拦截型能力（注册 before 钩子，可否决流程）
