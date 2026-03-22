import sqlite3
from typing import Optional, Dict, Any

DB_PATH = "users.db"

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        conn.commit()

def create_user(username: str, password_hash: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash)
        )
        conn.commit()
        return cursor.lastrowid

def get_user(username: str) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,)
        )
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "username": row[1], "password_hash": row[2]}
        return None

def get_user_by_id(id: int) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash FROM users WHERE id = ?",
            (id,)
        )
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "username": row[1], "password_hash": row[2]}
        return None