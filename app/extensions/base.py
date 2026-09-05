class BaseExtension:
    """所有扩展的统一基类。

    - load()：加载阶段的静态准备（如读配置），默认空实现，是【可选钩子】。
    - setup(ctx)：绑定阶段注入运行时上下文 ExtensionContext，子类【必须实现】，
      在里面用 ctx.bus 订阅事件、用 ctx.hooks 注册拦截钩子。
    """
    name = "base"

    def load(self):
        pass

    def setup(self, ctx):
        raise NotImplementedError("子类必须实现 setup(self, ctx)")
