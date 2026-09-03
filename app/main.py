
from fastapi import FastAPI
from app.agent import run_agent
from app.graph_agent import get_checkpointer, run_graph_agent
from app.schema import ChatRequest, ChatResponse
import logging
from app.db import init_db
logging.basicConfig(level=logging.INFO)


app = FastAPI(
    title = "fengmouren的聊天机器人",
    version = "0.2.0"
)

@ app.on_event("startup")
async def startup():
    init_db()
@app.get('/health')
def health():
    return {
        "status":"ok,也是成功打开了"
    }


@app.post("/api/chat",response_model = ChatResponse)
async def chat_endpoint(request:ChatRequest):
    reply = run_agent(request.message,session_id = request.session_id) #
    return {"reply": reply}


@app.post("/api/chat_graph",response_model = ChatResponse)
def chat_graph_endpoint(request:ChatRequest):
    """LangGraph 版 Agent（阶段 3）：带 Checkpoint 会话持久化。

    用 with 打开 SqliteSaver，session_id 映射为 thread_id，
    同一 session_id 的对话历史可跨请求/重启恢复。
    """
    with get_checkpointer() as checkpointer:
        reply = run_graph_agent(
            request.message,
            session_id = request.session_id,
            checkpointer = checkpointer,
        )
    return {"reply": reply}


# ===========================================================================
# 【新增 · 阶段 3 SSE 流式端点】由指导侧写入
# ===========================================================================
# 对比 /api/chat_graph：那个端点是"等全部生成完再返回"，
# 这个端点是"生成一个推一个"，前端用 EventSource 逐 token 接收（打字机效果）。
# 响应格式为 SSE（Server-Sent Events）：每条事件 "data: {json}\n\n"。
# 事件 type：
#   - "token"      ：LLM 正在生成的文本增量
#   - "tool_call"  ：模型发起工具调用（工具名）
#   - "done"       ：流式结束
#   - "error"      ：出错信息
import json as _json
from fastapi.responses import StreamingResponse as _StreamingResponse
from app.stream_graph import get_checkpointer as stream_get_checkpointer
from app.stream_graph import stream_graph_events

@app.post("/api/chat_graph_stream")
async def chat_graph_stream_endpoint(request: ChatRequest):
    """SSE 流式版 Agent：同一 session_id 的历史可跨请求恢复（Checkpoint）。"""
    async def event_generator():
        try:
            # 与 /api/chat_graph 一致：with 打开 SqliteSaver 做会话持久化
            with stream_get_checkpointer() as checkpointer:
                for event_type, data in stream_graph_events(
                    request.message,
                    session_id=request.session_id,
                    checkpointer=checkpointer,
                ):
                    payload = _json.dumps(
                        {"type": event_type, "content": data}, ensure_ascii=False
                    )
                    yield f"data: {payload}\n\n"
        except Exception as exc:  # 异常也要以 SSE 形式通知前端，避免连接悬挂
            yield f"data: {_json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"

    return _StreamingResponse(event_generator(), media_type="text/event-stream")
