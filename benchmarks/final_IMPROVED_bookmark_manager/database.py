import sqlite3
from typing import List, Optional, Dict, Any

DB_PATH = "bookmarks.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS bookmark_tags (
            bookmark_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (bookmark_id, tag_id),
            FOREIGN KEY (bookmark_id) REFERENCES bookmarks(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );
        """)

def add_bookmark(url: str, title: str, tags: List[str]):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO bookmarks (url, title) VALUES (?, ?)", (url, title))
        bookmark_id = cursor.lastrowid
        tag_ids = []
        for tag in set(tags):
            cursor.execute("SELECT id FROM tags WHERE name = ?", (tag,))
            row = cursor.fetchone()
            if row:
                tag_id = row["id"]
            else:
                cursor.execute("INSERT INTO tags (name) VALUES (?)", (tag,))
                tag_id = cursor.lastrowid
            tag_ids.append(tag_id)
        for tag_id in tag_ids:
            cursor.execute("INSERT INTO bookmark_tags (bookmark_id, tag_id) VALUES (?, ?)", (bookmark_id, tag_id))
        conn.commit()

def get_bookmarks(tag: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        if tag is None:
            cursor.execute("""
            SELECT b.id, b.url, b.title,
                GROUP_CONCAT(t.name, ',') AS tags
            FROM bookmarks b
            LEFT JOIN bookmark_tags bt ON b.id = bt.bookmark_id
            LEFT JOIN tags t ON bt.tag_id = t.id
            GROUP BY b.id
            ORDER BY b.id
            """)
        else:
            cursor.execute("""
            SELECT b.id, b.url, b.title,
                GROUP_CONCAT(t.name, ',') AS tags
            FROM bookmarks b
            JOIN bookmark_tags bt ON b.id = bt.bookmark_id
            JOIN tags t ON bt.tag_id = t.id
            WHERE b.id IN (
                SELECT bookmark_id FROM bookmark_tags
                WHERE tag_id = (SELECT id FROM tags WHERE name = ?)
            )
            GROUP BY b.id
            ORDER BY b.id
            """, (tag,))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            tags_list = row["tags"].split(",") if row["tags"] else []
            results.append({
                "id": row["id"],
                "url": row["url"],
                "title": row["title"],
                "tags": tags_list
            })
        return results

def search_bookmarks(query: str) -> List[Dict[str, Any]]:
    like_query = f"%{query}%"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT b.id, b.url, b.title,
            GROUP_CONCAT(t.name, ',') AS tags
        FROM bookmarks b
        LEFT JOIN bookmark_tags bt ON b.id = bt.bookmark_id
        LEFT JOIN tags t ON bt.tag_id = t.id
        WHERE b.url LIKE ? OR b.title LIKE ?
        GROUP BY b.id
        ORDER BY b.id
        """, (like_query, like_query))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            tags_list = row["tags"].split(",") if row["tags"] else []
            results.append({
                "id": row["id"],
                "url": row["url"],
                "title": row["title"],
                "tags": tags_list
            })
        return results

def delete_bookmark(id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bookmarks WHERE id = ?", (id,))
        if cursor.rowcount == 0:
            raise ValueError(f"Bookmark with id {id} not found")
        conn.commit()