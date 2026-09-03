from typing import TypedDict,Annotated
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage,ToolMessage

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    session_id: str

def agent_node(state: AgentState) -> dict:
    if state["messages"] and isinstance(state["messages"][-1], ToolMessage) :
        return {"messages": [AIMessage(content="1+1=2，这是计算结果")], "session_id": state["session_id"]}    # 看到结果 → 直接作答
       
    else:
        return {"messages": [AIMessage(content="我需要计算一下", tool_calls=[{ "name": "calculator",  "args": {"expression": "1+1"},"id": "tool_1","type": "tool_call",}])], "session_id": state["session_id"]}  # 没看到 → 调工具
     
def tool_node(state: AgentState) -> dict:

    return {"messages":[ToolMessage(content="1+1=2", tool_call_id="tool_1")], "session_id": state["session_id"]}

def should_continue(state: AgentState) -> str:
    """条件边：最后一条是带 tool_calls 的 AIMessage 就继续走 tools，否则结束。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END

builder = StateGraph(AgentState)
builder.add_node("agent", agent_node)
builder.add_node("tools", tool_node)
builder.add_edge(START, "agent")
builder.add_conditional_edges(
    "agent", should_continue, {"tools": "tools", END: END}
)
builder.add_edge("tools", "agent")
graph = builder.compile()

result = graph.invoke({"messages": [], "session_id": "12345"})
print(result)