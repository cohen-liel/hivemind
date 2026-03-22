from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from typing import List, Optional
import database

app = FastAPI()

class BookmarkCreate(BaseModel):
    url: HttpUrl
    title: str
    tags: List[str] = []

class BookmarkOut(BaseModel):
    id: int
    url: HttpUrl
    title: str
    tags: List[str] = []

@app.post("/bookmarks", response_model=BookmarkOut)
def create_bookmark(bookmark: BookmarkCreate):
    bookmark_id = database.add_bookmark(bookmark.url, bookmark.title, bookmark.tags)
    # Return the created bookmark with id
    # Since add_bookmark does not return id, we fetch the last inserted id by querying bookmarks with url and title
    bookmarks = database.search_bookmarks(bookmark.url)
    for b in bookmarks:
        if b["title"] == bookmark.title:
            return BookmarkOut(**b)
    # fallback
    raise HTTPException(status_code=500, detail="Failed to create bookmark")

@app.get("/bookmarks", response_model=List[BookmarkOut])
def get_bookmarks(tag: Optional[str] = Query(None)):
    bookmarks = database.get_bookmarks(tag)
    return [BookmarkOut(**b) for b in bookmarks]

@app.get("/bookmarks/search", response_model=List[BookmarkOut])
def search_bookmarks(q: str = Query(...)):
    bookmarks = database.search_bookmarks(q)
    return [BookmarkOut(**b) for b in bookmarks]

@app.delete("/bookmarks/{id}", status_code=204)
def delete_bookmark(id: int):
    try:
        database.delete_bookmark(id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Resource not found")