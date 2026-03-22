from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from database import get_connection, init_db

app = FastAPI()

class TodoCreate(BaseModel):
    title: str
    description: str = ""
    completed: bool = False

class TodoUpdate(BaseModel):
    title: str
    description: str = ""
    completed: bool

class TodoOut(BaseModel):
    id: int
    title: str
    description: str
    completed: bool

@app.on_event("startup")
def startup():
    init_db()

@app.post("/todos", response_model=TodoOut, status_code=201)
def create_todo(todo: TodoCreate):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO todos (title, description, completed) VALUES (?, ?, ?)",
        (todo.title, todo.description, todo.completed)
    )
    conn.commit()
    todo_id = cursor.lastrowid
    cursor.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
    row = cursor.fetchone()
    conn.close()
    return TodoOut(**row)

@app.get("/todos", response_model=List[TodoOut])
def read_todos():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM todos")
    rows = cursor.fetchall()
    conn.close()
    return [TodoOut(**row) for row in rows]

@app.get("/todos/{id}", response_model=TodoOut)
def read_todo(id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM todos WHERE id = ?", (id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return TodoOut(**row)

@app.put("/todos/{id}", response_model=TodoOut)
def update_todo(id: int, todo: TodoUpdate):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM todos WHERE id = ?", (id,))
    existing = cursor.fetchone()
    if existing is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Todo not found")
    cursor.execute(
        "UPDATE todos SET title = ?, description = ?, completed = ? WHERE id = ?",
        (todo.title, todo.description, todo.completed, id)
    )
    conn.commit()
    cursor.execute("SELECT * FROM todos WHERE id = ?", (id,))
    updated = cursor.fetchone()
    conn.close()
    return TodoOut(**updated)

@app.delete("/todos/{id}", status_code=204)
def delete_todo(id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM todos WHERE id = ?", (id,))
    existing = cursor.fetchone()
    if existing is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Todo not found")
    cursor.execute("DELETE FROM todos WHERE id = ?", (id,))
    conn.commit()
    conn.close()