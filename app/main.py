
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.graph_agent import run_graph_agent_detailed
from app.graph_agent_hitl import get_checkpointer as hitl_get_checkpointer
from app.graph_agent_hitl import resume_hitl_turn, start_hitl_turn
from app.schema import ChatRequest, ChatResponse, HitlResumeRequest
import logging
from app.session_store import build_chain, init_store
logging.basicConfig(level=logging.INFO)


app = FastAPI(
    title = "fengmouren的聊天机器人",
    version = "0.3.1",
)

# CORS：允许前端从 file:// 或其他源访问 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载前端静态页面：访问 http://127.0.0.1:8000/ 直接打开聊天界面
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(WEB_DIR / "index.html"))

@app.get('/health')
def health():
    return {
        "status":"ok,也是成功打开了"
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    """统一入口：LangGraph 版 Agent。

    历史由阶段6树形存储（sessions.db）管理：run_graph_agent 每轮 build_chain
    重建完整历史注入图，因此这里【不再传 checkpointer】——否则 LangGraph 会把
    checkpoint 里的旧 state 与注入的历史再合并一遍，导致消息重复、工具配对错乱。
    Checkpoint 仅 HITL 路径（graph_agent_hitl.py，需要 interrupt/resume）使用。
    """
    reply_info = run_graph_agent_detailed(
        request.message,
        session_id=request.session_id,
    )
    return {"reply": reply_info["reply"], "tool_trace": reply_info["tool_trace"]}


# ===========================================================================
# 【新增 · 阶段 3 SSE 流式端点】由指导侧写入
# ===========================================================================
# 对比 /api/chat：那个端点是"等全部生成完再返回"，
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
            # 与 /api/chat 一致：with 打开 SqliteSaver 做会话持久化
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


# ===========================================================================
# 【新增 · HITL 两段式审批端点】危险工具（run_command）先 interrupt 暂停图，
# 把待审批内容返回给前端；前端展示后调 /resume 提交 approve/reject 恢复执行。
# 与 /api/chat 的区别：历史存在 Checkpoint（interrupt/resume 依赖它持久化状态），
# 而不是树形存储——Checkpoint 现在只服务 HITL 路径。
# ===========================================================================
@app.post("/api/chat_hitl")
def chat_hitl_endpoint(request: ChatRequest):
    """HITL 第 1 段：发起对话。

    - 返回 {"status": "done", "reply": ...}：本轮没触发审批，直接拿到回答；
    - 返回 {"status": "interrupted", "pending_approval": {"tool", "args"}}：
      危险工具待审批，前端展示后调 /api/chat_hitl/resume 提交决策。
    """
    with hitl_get_checkpointer() as cp:
        return start_hitl_turn(request.message, request.session_id, cp)


@app.post("/api/chat_hitl/resume")
def chat_hitl_resume_endpoint(request: HitlResumeRequest):
    """HITL 第 2 段：提交人工决策（approve / reject），恢复被中断的图。

    若本轮还有第二个危险工具，会再次返回 interrupted，前端继续审批即可。
    会话不在中断状态时返回 {"status": "no_pending_approval"}。
    """
    with hitl_get_checkpointer() as cp:
        return resume_hitl_turn(request.decision, request.session_id, cp)


# ===========================================================================
# 【新增 · v0.3.1 会话历史端点】修复前端"点击旧会话聊天框空白"：
# 树形存储（sessions.db）是历史唯一事实源（/api/chat 每轮 build_chain 重建
# 历史再 persist_messages 追加），但此前没有任何 GET 接口把它读出来给前端。
# ===========================================================================
@app.get("/api/sessions/{session_id}/history")
def session_history(session_id: str):
    """返回某会话当前链（根 → 叶子）的完整历史消息，供前端回显。

    - user      → {"role": "user", "content": ...}
    - assistant → {"role": "assistant", "content": ..., "tool_calls": [...]}
    - tool      → {"role": "tool", "content": ..., "tool_call_id": ...}
      （前端按 tool_call_id 把结果配对进对应的工具调用卡片）
    - system / compaction / fork 是内部节点（LLM 上下文/分支元数据），不返回。
    - 空会话返回 {"count": 0, "messages": []}，不报错。
    """
    init_store()  # 幂等：防御性建表，避免库还没初始化时查询报错
    chain = build_chain(session_id)

    messages = []
    for e in chain:
        etype = e["type"]
        if etype == "user":
            messages.append({"role": "user", "content": e.get("content") or ""})
        elif etype == "assistant":
            messages.append({
                "role": "assistant",
                "content": e.get("content") or "",
                "tool_calls": e.get("tool_calls") or [],
            })
        elif etype == "tool":
            messages.append({
                "role": "tool",
                "content": e.get("content") or "",
                "tool_call_id": e.get("tool_call_id") or "",
            })

    return {"session_id": session_id, "count": len(messages), "messages": messages}
