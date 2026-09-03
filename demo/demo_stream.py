"""SSE 流式演示脚本（阶段 3 · 新增演示文件）。

在项目根目录运行：
    .venv\\Scripts\\python.exe -m demo.demo_stream

演示两种场景的流式事件产出：
1. 简单对话：看 ("token", 文本片段) 逐段流出（打字机效果）
2. 复杂计算：模型算不准，倾向调用计算器工具，看 ("tool_call", 工具名) 事件
3. 带 Checkpoint：验证会话持久化路径（与 FastAPI 端点一致）
"""

from app.stream_graph import get_checkpointer, stream_graph_events


def run(label: str, message: str, session_id: str, use_checkpoint: bool = False):
    print(f"\n{'=' * 60}\n[{label}] 输入：{message}\n{'=' * 60}")
    if use_checkpoint:
        # 与 FastAPI 端点一致的调用方式：with 打开 SqliteSaver
        with get_checkpointer() as checkpointer:
            for event_type, data in stream_graph_events(
                message, session_id, checkpointer=checkpointer
            ):
                _emit(event_type, data)
    else:
        for event_type, data in stream_graph_events(message, session_id):
            _emit(event_type, data)


def _emit(event_type: str, data):
    if event_type == "token":
        print(data, end="", flush=True)
    elif event_type == "tool_call":
        print(f"\n[⚙ 正在调用工具：{data}]", flush=True)
    elif event_type == "done":
        print("\n[done] 流式结束", flush=True)


if __name__ == "__main__":
    # 场景 1：简单对话（打字机效果）
    run("简单对话", "用一句话介绍你自己", "demo_sse_1")
    # 场景 2：复杂计算，触发工具调用
    run("复杂计算", "938472 * 128473 等于多少？请使用计算工具", "demo_sse_2")
    # 场景 3：带 Checkpoint 的会话（与 FastAPI 端点一致）
    run("Checkpoint 对话", "你好，我叫小明", "demo_sse_3", use_checkpoint=True)
