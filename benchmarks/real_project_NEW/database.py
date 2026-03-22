import sqlite3
from typing import Optional, List, Dict

DB_FILE = 'chat.db'


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Initialize the database by creating agents and messages tables if they do not exist.
    """
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
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(sender_id) REFERENCES agents(id),
                FOREIGN KEY(receiver_id) REFERENCES agents(id)
            )
        """)
        # Create indexes to optimize queries on sender_id and receiver_id
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender_id ON messages(sender_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_receiver_id ON messages(receiver_id)")
        conn.commit()


def create_agent(name: str, role: str) -> Dict:
    """
    Insert a new agent into the agents table.
    Returns the created agent as a dict.
    Raises sqlite3.IntegrityError if name is not unique.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO agents (name, role) VALUES (?, ?)",
            (name, role)
        )
        agent_id = cursor.lastrowid
        cursor.execute(
            "SELECT id, name, role, created_at FROM agents WHERE id = ?",
            (agent_id,)
        )
        row = cursor.fetchone()
        return dict(row)


def get_agents() -> List[Dict]:
    """
    Retrieve all agents as a list of dicts.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, role, created_at FROM agents ORDER BY id")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_agent(agent_id: int) -> Optional[Dict]:
    """
    Retrieve a single agent by id.
    Returns dict if found, else None.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, role, created_at FROM agents WHERE id = ?",
            (agent_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_message(sender_id: int, receiver_id: int, content: str) -> Dict:
    """
    Insert a new message into the messages table.
    Returns the created message as a dict.
    Raises sqlite3.IntegrityError if foreign keys invalid.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
            (sender_id, receiver_id, content)
        )
        message_id = cursor.lastrowid
        cursor.execute(
            "SELECT id, sender_id, receiver_id, content, timestamp FROM messages WHERE id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        return dict(row)


def get_conversation(sender_id: int, receiver_id: int) -> List[Dict]:
    """
    Retrieve all messages exchanged between sender_id and receiver_id,
    ordered by timestamp ascending.
    Includes messages sent in both directions.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, sender_id, receiver_id, content, timestamp
            FROM messages
            WHERE (sender_id = ? AND receiver_id = ?)
               OR (sender_id = ? AND receiver_id = ?)
            ORDER BY timestamp ASC, id ASC
            """,
            (sender_id, receiver_id, receiver_id, sender_id)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_agent_messages(agent_id: int) -> List[Dict]:
    """
    Retrieve all messages where the agent is either sender or receiver,
    ordered by timestamp ascending.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, sender_id, receiver_id, content, timestamp
            FROM messages
            WHERE sender_id = ? OR receiver_id = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (agent_id, agent_id)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
