import sqlite3
from typing import List, Optional, Dict, Any

DB_PATH = "bookmarks.db"

def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bookmark_tags (
            bookmark_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            FOREIGN KEY(bookmark_id) REFERENCES bookmarks(id) ON DELETE CASCADE,
            FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY(bookmark_id, tag_id)
        )
    """)
    conn.commit()
    conn.close()

def add_bookmark(url: str, title: str, tags: List[str]):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO bookmarks (url, title) VALUES (?, ?)", (url, title))
    bookmark_id = c.lastrowid
    for tag in tags:
        tag = tag.strip().lower()
        if not tag:
            continue
        c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
        c.execute("SELECT id FROM tags WHERE name = ?", (tag,))
        tag_id = c.fetchone()[0]
        c.execute("INSERT INTO bookmark_tags (bookmark_id, tag_id) VALUES (?, ?)", (bookmark_id, tag_id))
    conn.commit()
    conn.close()

def get_bookmarks(tag: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    if tag:
        tag = tag.strip().lower()
        c.execute("""
            SELECT b.id, b.url, b.title
            FROM bookmarks b
            JOIN bookmark_tags bt ON b.id = bt.bookmark_id
            JOIN tags t ON bt.tag_id = t.id
            WHERE t.name = ?
            ORDER BY b.id DESC
        """, (tag,))
    else:
        c.execute("SELECT id, url, title FROM bookmarks ORDER BY id DESC")
    bookmarks = []
    rows = c.fetchall()
    for row in rows:
        bookmark_id, url, title = row
        c.execute("""
            SELECT t.name
            FROM tags t
            JOIN bookmark_tags bt ON t.id = bt.tag_id
            WHERE bt.bookmark_id = ?
            ORDER BY t.name
        """, (bookmark_id,))
        tags = [r[0] for r in c.fetchall()]
        bookmarks.append({
            "id": bookmark_id,
            "url": url,
            "title": title,
            "tags": tags
        })
    conn.close()
    return bookmarks

def search_bookmarks(query: str) -> List[Dict[str, Any]]:
    query = f"%{query.lower()}%"
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, url, title
        FROM bookmarks
        WHERE LOWER(url) LIKE ? OR LOWER(title) LIKE ?
        ORDER BY id DESC
    """, (query, query))
    bookmarks = []
    rows = c.fetchall()
    for row in rows:
        bookmark_id, url, title = row
        c.execute("""
            SELECT t.name
            FROM tags t
            JOIN bookmark_tags bt ON t.id = bt.tag_id
            WHERE bt.bookmark_id = ?
            ORDER BY t.name
        """, (bookmark_id,))
        tags = [r[0] for r in c.fetchall()]
        bookmarks.append({
            "id": bookmark_id,
            "url": url,
            "title": title,
            "tags": tags
        })
    conn.close()
    return bookmarks

def delete_bookmark(id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM bookmarks WHERE id = ?", (id,))
    if c.fetchone() is None:
        conn.close()
        return False
    c.execute("DELETE FROM bookmarks WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return True