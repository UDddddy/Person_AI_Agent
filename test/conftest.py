# pytest 共享 fixture 在此定义。
# 旧版 tmp_db fixture（阶段1-2 的 database.db）已随遗留代码一并移除，
# 当前测试各自使用临时路径或 sessions.db 的独立 session_id 做隔离。
