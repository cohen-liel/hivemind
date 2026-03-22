import sqlite3
from typing import List, Optional, Dict

DB_PATH = "todos.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            done INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def add_todo(title: str, description: Optional[str] = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO todos (title, description, done) VALUES (?, ?, 0)",
        (title, description)
    )
    conn.commit()
    todo_id = cursor.lastrowid
    conn.close()
    return todo_id

def get_todos() -> List[Dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, description, done FROM todos")
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "done": bool(row["done"])
        }
        for row in rows
    ]

def get_todo(id: int) -> Optional[Dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, description, done FROM todos WHERE id = ?", (id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "done": bool(row["done"])
    }

def update_todo(id: int, done: bool) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE todos SET done = ? WHERE id = ?", (int(done), id))
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated

def delete_todo(id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM todos WHERE id = ?", (id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted