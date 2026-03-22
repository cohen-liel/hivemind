from fastapi import FastAPI, HTTPException
from typing import List, Optional
from pydantic import BaseModel
import database

app = FastAPI()

class TodoCreate(BaseModel):
    title: str
    description: Optional[str] = None

class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    done: Optional[bool] = None

class Todo(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    done: bool

@app.on_event("startup")
def startup_event():
    database.init_db()

@app.post("/todos", response_model=Todo)
def create_todo(todo: TodoCreate):
    todo_id = database.add_todo(todo.title, todo.description)
    db_todo = database.get_todo(todo_id)
    if db_todo is None:
        raise HTTPException(status_code=500, detail="Failed to create todo")
    return db_todo

@app.get("/todos", response_model=List[Todo])
def read_todos():
    return database.get_todos()

@app.get("/todos/{id}", response_model=Todo)
def read_todo(id: int):
    db_todo = database.get_todo(id)
    if db_todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return db_todo

@app.put("/todos/{id}", response_model=Todo)
def update_todo(id: int, todo: TodoUpdate):
    existing = database.get_todo(id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    title = todo.title if todo.title is not None else existing["title"]
    description = todo.description if todo.description is not None else existing["description"]
    done = todo.done if todo.done is not None else existing["done"]
    updated = database.update_todo(id, title, description, done)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update todo")
    return database.get_todo(id)

@app.delete("/todos/{id}", status_code=204)
def delete_todo(id: int):
    deleted = database.delete_todo(id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Todo not found")