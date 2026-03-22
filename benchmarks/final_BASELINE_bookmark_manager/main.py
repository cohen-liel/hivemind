from fastapi import FastAPI, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel, HttpUrl
from database import get_connection, init_db

app = FastAPI()

class BookmarkCreate(BaseModel):
    url: HttpUrl
    title: str
    tags: Optional[List[str]] = []

class Bookmark(BaseModel):
    id: int
    url: HttpUrl
    title: str
    tags: List[str]

def row_to_bookmark(row, tags):
    return Bookmark(id=row[0], url=row[1], title=row[2], tags=tags)

def get_tags_for_bookmark(conn, bookmark_id: int) -> List[str]:
    c = conn.cursor()
    c.execute("SELECT tag FROM tags WHERE bookmark_id = ?", (bookmark_id,))
    return [r[0] for r in c.fetchall()]

@app.on_event("startup")
def startup():
    init_db()

@app.post("/bookmarks", response_model=Bookmark, status_code=201)
def create_bookmark(bm: BookmarkCreate):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO bookmarks (url, title) VALUES (?, ?)", (bm.url, bm.title))
    bookmark_id = c.lastrowid
    if bm.tags:
        c.executemany("INSERT INTO tags (bookmark_id, tag) VALUES (?, ?)", [(bookmark_id, tag) for tag in bm.tags])
    conn.commit()
    tags = bm.tags or []
    conn.close()
    return Bookmark(id=bookmark_id, url=bm.url, title=bm.title, tags=tags)

@app.get("/bookmarks", response_model=List[Bookmark])
def get_bookmarks(tag: Optional[str] = Query(None)):
    conn = get_connection()
    c = conn.cursor()
    if tag:
        c.execute("""
            SELECT b.id, b.url, b.title FROM bookmarks b
            JOIN tags t ON b.id = t.bookmark_id
            WHERE t.tag = ?
            GROUP BY b.id
            ORDER BY b.id
        """, (tag,))
    else:
        c.execute("SELECT id, url, title FROM bookmarks ORDER BY id")
    rows = c.fetchall()
    bookmarks = []
    for row in rows:
        tags = get_tags_for_bookmark(conn, row[0])
        bookmarks.append(row_to_bookmark(row, tags))
    conn.close()
    return bookmarks

@app.get("/bookmarks/search", response_model=List[Bookmark])
def search_bookmarks(q: str = Query(..., min_length=1)):
    conn = get_connection()
    c = conn.cursor()
    like_q = f"%{q}%"
    c.execute("""
        SELECT id, url, title FROM bookmarks
        WHERE url LIKE ? OR title LIKE ?
        ORDER BY id
    """, (like_q, like_q))
    rows = c.fetchall()
    bookmarks = []
    for row in rows:
        tags = get_tags_for_bookmark(conn, row[0])
        bookmarks.append(row_to_bookmark(row, tags))
    conn.close()
    return bookmarks

@app.delete("/bookmarks/{id}", status_code=204)
def delete_bookmark(id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM bookmarks WHERE id = ?", (id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Resource not found")
    c.execute("DELETE FROM tags WHERE bookmark_id = ?", (id,))
    c.execute("DELETE FROM bookmarks WHERE id = ?", (id,))
    conn.commit()
    conn.close()