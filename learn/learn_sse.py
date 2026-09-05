# 【历史学习记录】本脚本依赖阶段1的 app.llm（旧 OpenAI client），
# 该模块已在项目清理中删除（被阶段7 Provider 抽象层替代）。
# 保留此文件仅作学习参考，无法直接运行。
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
