from app.llm import client
from app.config import setting

response = client.chat.completions.create(
    model = setting.llm_model,
    messages = [
        {"role": "system", "content": "你是一个专业助手"},
        {"role": "user", "content": "简单的介绍一下自己?"}
    ]
    ,
    stream = True
)
for chunk in response:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
        