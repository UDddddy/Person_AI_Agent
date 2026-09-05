from typing import Any

from pydantic import BaseModel, field_validator

class ChatRequest(BaseModel):
    message:str
    session_id:str = "default_session"

class ChatResponse(BaseModel):
    reply:str
    # 工具执行轨迹（按调用顺序）：每项 {name, args, result, latency, blocked}。
    # 前端据此渲染"工具调用时间线"；纯聊天轮次为空列表。
    tool_trace: list[dict[str, Any]] = []

class HitlResumeRequest(BaseModel):
    """HITL 两段式第 2 段请求：提交人工审批决策。"""
    session_id:str = "default_session"
    decision:str  # "approve" / "reject"

    @field_validator("decision")
    @classmethod
    def decision_must_be_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("approve", "reject"):
            raise ValueError("decision 必须是 approve 或 reject")
        return v