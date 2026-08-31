from openai import OpenAI
from app.config import setting

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