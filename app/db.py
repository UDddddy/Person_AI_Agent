import sqlite3
from pathlib import Path
DB_PATH = Path(__file__).resolve().parent / "database.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)

            ''')
        conn.commit()
    finally:
        conn.close()

def save_message(session_id,role,content):
    conn =  get_db_connection() 
    try:

        conn.execute('''
            INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)
            ''',(session_id, role, content))
        conn.commit()
    finally:
        conn.close()

def load_history(session_id):
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC
            ''', (session_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()