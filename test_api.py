import httpx

base = "http://127.0.0.1:8000/api/chat"
sid = "session_demo_1"   # 两轮用同一个 session_id → 同一场对话

def ask(message):
    r = httpx.post(base, json={"message": message, "session_id": sid})
    print(f"你: {message}")
    print(f"AI: {r.json()['reply']}")
    print("-" * 30)
    

# 连续两条，看第二条是否"记得"第一条
ask("请计算 1+1")
ask("刚才算的结果再加上 10 是多少？")
r = httpx.post(base, json={"message": "我刚才让你计算的第一个问题是什么？", "session_id": sid})
print(f"AI: {r.json()['reply']}")