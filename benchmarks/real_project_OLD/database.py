import sqlite3
from typing import Optional, List, Dict

DB_FILE = 'chat.db'


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL REFERENCES agents(id),
                receiver_id INTEGER NOT NULL REFERENCES agents(id),
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def create_agent(name: str, role: str) -> Dict:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO agents (name, role) VALUES (?, ?)",
            (name, role)
        )
        agent_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            "SELECT id, name, role, created_at FROM agents WHERE id = ?",
            (agent_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_agents() -> List[Dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, role, created_at FROM agents ORDER BY id")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_agent(agent_id: int) -> Optional[Dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, role, created_at FROM agents WHERE id = ?",
            (agent_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_message(sender_id: int, receiver_id: int, content: str) -> Dict:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
            (sender_id, receiver_id, content)
        )
        message_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            "SELECT id, sender_id, receiver_id, content, timestamp FROM messages WHERE id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_conversation(sender_id: int, receiver_id: int) -> List[Dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, sender_id, receiver_id, content, timestamp
            FROM messages
            WHERE (sender_id = ? AND receiver_id = ?)
               OR (sender_id = ? AND receiver_id = ?)
            ORDER BY timestamp ASC, id ASC
        """, (sender_id, receiver_id, receiver_id, sender_id))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_agent_messages(agent_id: int) -> List[Dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, sender_id, receiver_id, content, timestamp
            FROM messages
            WHERE sender_id = ? OR receiver_id = ?
            ORDER BY timestamp ASC, id ASC
        """, (agent_id, agent_id))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
