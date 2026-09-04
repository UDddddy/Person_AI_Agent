"""阶段 5 · 第二课验证：observability 扩展 + ToolPipeline 事件链路。

从项目根目录运行：
    .venv\\Scripts\\python.exe -m demo.demo_observability
"""

from app.event_bus import EventBus, EVENT_AGENT_START
from app.extensions.observability import setup_observability
from tools.pipeline import ToolPipeline


def main():
    # 1. 建一条事件总线（事件的"广播站"）
    bus = EventBus()

    # 2. 挂上 observability 扩展：订阅所有事件 -> 自动打结构化日志
    setup_observability(bus)

    # 3. 让工具管道持有同一根总线（pipeline 执行前后会往这根总线 emit）
    pipeline = ToolPipeline(bus=bus)

    # 4. 手动发一个 agent_start 事件，演示"非工具事件"也能被同一套日志记录
    bus.emit(EVENT_AGENT_START, {"message": "你好，帮我算 1+1"})

    # 5. 真正走一次工具：预期按顺序看到 tool_call -> tool_result 两条日志
    result = pipeline.execute("calculator", {"expression": "1+1"})
    print("最终结果:", result)

    # 6. 解耦验证：再建一根"没挂扩展"的总线，执行时应一条日志都不打印
    print("---- 下面这行没有结构化日志，证明不订阅就静默（解耦）----")
    quiet_pipeline = ToolPipeline(bus=EventBus())
    print("静默管道结果:", quiet_pipeline.execute("calculator", {"expression": "2+3"}))


if __name__ == "__main__":
    main()
