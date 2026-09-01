
from fastapi import FastAPI
from app.agent import run_agent
from app.schema import ChatRequest, ChatResponse
import logging
from app.db import init_db
logging.basicConfig(level=logging.INFO)


app = FastAPI(
    title = "fengmouren的聊天机器人",
    version = "0.1.0"
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
