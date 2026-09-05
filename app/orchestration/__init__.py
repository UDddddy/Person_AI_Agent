"""阶段8：多 Agent 编排。

三种协作模式：
- AgentChain：顺序流水线，$INPUT/$ORIGINAL 变量替换
- AgentTeam：调度器模式，主 Agent 委派子 Agent
- Subagent：后台并行，无头子 Agent 事件上报

主线落地：
- JobAssistant：求职助手（planner→builder→reviewer）
"""

from app.orchestration.chain import AgentChain, ChainStep, ChainResult, StepResult
from app.orchestration.team import AgentTeam, SubAgent, DispatchResult
from app.orchestration.subagent import (
    HeadlessSubagent, SubagentRunner, SubagentSpec, SubagentResult,
    EVENT_SUBAGENT_START, EVENT_SUBAGENT_DONE, EVENT_SUBAGENT_ERROR,
)
from app.orchestration.job_assistant import JobAssistant

__all__ = [
    "AgentChain", "ChainStep", "ChainResult", "StepResult",
    "AgentTeam", "SubAgent", "DispatchResult",
    "HeadlessSubagent", "SubagentRunner", "SubagentSpec", "SubagentResult",
    "EVENT_SUBAGENT_START", "EVENT_SUBAGENT_DONE", "EVENT_SUBAGENT_ERROR",
    "JobAssistant",
]
