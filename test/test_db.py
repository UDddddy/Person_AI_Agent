from app.db import save_message,load_history

def test_save_and_load(tmp_db):          # ← 必须传 tmp_db
    save_message("s1", "user", "你好")    # ← 先存
    save_message("s1", "assistant", "你好呀")
    assert load_history("s1") == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
    ]

def test_load_empty(tmp_db):
    assert load_history("none") == []     # 无记录 → 空列表

def test_session_isolation(tmp_db):
    save_message("s1", "user", "a")
    assert load_history("s2") == []       # s2 读不到 s1 的（这才是"隔离"）

def test_history_order(tmp_db):

    save_message("session_demo_3","user","第一条消息")
    save_message("session_demo_3","assistant","第一条回复")
    save_message("session_demo_3","user","第二条消息")
    save_message("session_demo_3","assistant","第二条回复")
    assert load_history("session_demo_3") == [
        {"role":"user","content":"第一条消息"},
        {"role":"assistant","content":"第一条回复"},
        {"role":"user","content":"第二条消息"},
        {"role":"assistant","content":"第二条回复"}
    ]