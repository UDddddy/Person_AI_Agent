from .base import BaseExtension


class ExtensionManager:
    def __init__(self):
        self.extensions = []            # 登记在册的扩展列表

    def register(self, ext):
        """加载阶段：只登记，不注入任何运行时能力。"""
        if not isinstance(ext, BaseExtension):
            raise TypeError(f"扩展必须继承 BaseExtension，收到 {type(ext).__name__}")
        self.extensions.append(ext)

    def bind_all(self, ctx):
        """绑定阶段：把运行时上下文 ctx 注入每个扩展，先 load() 再 setup(ctx)。"""
        for ext in self.extensions:
            ext.load()
            ext.setup(ctx)
