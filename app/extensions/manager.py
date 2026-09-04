from .base import BaseExtension

class ExtensionManager:
    def __init__(self):
        self.extensions = []            # 提示：登记在册的扩展列表
    def register(self, ext):
        """加载阶段：只登记，不注入任何运行时"""
        if not isinstance(ext, BaseExtension):       # 提示：请继承 BaseExtension
            raise TypeError(f"扩展必须继承 BaseExtension，收到 {type(ext).__name__}")
        self.extensions.append(ext)   # 任务：把 ext 加进列表即可，先别碰 bus
    def bind_all(self, bus):
        """绑定阶段：遍历每个扩展，先 load() 再 setup(bus)"""
        # 任务：for 循环，依次 ext.load(); ext.setup(bus)
        for ext in self.extensions:
            ext.load()
            ext.setup(bus)
