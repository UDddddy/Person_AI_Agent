
from fastapi import FastAPI
from app.llm import chat
from app.schema import ChatRequest, ChatResponse

app = FastAPI(
    title = "fengmouren的聊天机器人",
    version = "0.1.0"
)

@app.get('health')
def health():
    return {
        "status":"ok,也是成功打开了"
    }


@app.post("/api/chat",response_model = ChatResponse)
def chat_api(request:ChatRequest):
    answer = chat(request.message)

    return ChatResponse(answer=answer)
