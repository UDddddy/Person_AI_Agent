from app.llm import chat_with_tools
from tools.executor import execute_tool
import logging
from app.db import save_message, load_history
logger  =   logging.getLogger(__name__)
iterations = 5
def run_agent(user_message: str,session_id: str = "default_session",max_iterations: int = iterations):


    history = load_history(session_id)

    messages = [
        {"role": "system","content": "You are a helpful assistant."},
        *history,
        {"role": "user", "content": user_message }
    ]
    logger.info(f"用户消息: {user_message}")

    for i in range(max_iterations):
        logger.info(f"第 {i+1} 轮迭代")
        # 1. 把完整的 messages 交给 LLM
        response = chat_with_tools(messages)

        # 2. 没有 Tool Call，说明得到最终答案
        if not response.tool_calls:
            save_message(session_id, "user", user_message)
            save_message(session_id, "assistant", response.content)
            return response.content

        # 3. 保存 Assistant 的 Tool Call
        messages.append({
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments
                    }
                }
                for tool_call in response.tool_calls
            ]
        })
   
        # 4. 执行 Tool
        for tool_call in response.tool_calls:
            logger.info(f"模型调用工具：{tool_call.function.name}，参数：{tool_call.function.arguments}")
            result = execute_tool(tool_call.function.name,
                                    tool_call.function.arguments)

            # 5. 把 Tool Result 放回上下文
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result)
            })
  
    logger.warning(f"达到最大迭代次数")
    save_message(session_id, "user", user_message)                # ← 补这两行
    save_message(session_id, "assistant", "达到最大迭代次数，未能得到最终答案。")
    return "达到最大迭代次数，未能得到最终答案。"