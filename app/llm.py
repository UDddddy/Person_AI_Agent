from openai import OpenAI
from app.config import setting
from tools.registry import TOOL_SCHEMA

client = OpenAI(
    api_key = setting.llm_api_key,
    base_url = setting.llm_base_url
)


def chat(message:str) -> str:
    response = client.chat.completions.create(
        model = setting.llm_model,
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": message}
        ]
    )

    return response.choices[0].message.content

def chat_with_tools(message:str)-> str:
    response = client.chat.completions.create(
        model = setting.llm_model,
        messages = message,
        tools = TOOL_SCHEMA
    )

    return response.choices[0].message  