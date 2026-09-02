
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
