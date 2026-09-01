
from fastapi import FastAPI
from app.agent import run_agent
from app.schema import ChatRequest, ChatResponse
import logging
logging.basicConfig(level=logging.INFO)


app = FastAPI(
    title = "fengmouren的聊天机器人",
    version = "0.1.0"
)

@app.get('/health')
def health():
    return {
        "status":"ok,也是成功打开了"
    }


@app.post("/api/chat",response_model = ChatResponse)
async def chat_endpoint(request:ChatRequest):
    reply = run_agent(request.message)
    return {"reply": reply}
