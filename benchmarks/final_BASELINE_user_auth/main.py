from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
import hashlib
import secrets
from typing import Optional, Dict
from database import init_db, DB_PATH
import sqlite3

app = FastAPI()

init_db()

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

tokens: Dict[str, str] = {}

class UserCredentials(BaseModel):
    username: str
    password: str

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization[7:]
    username = tokens.get(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")
    return username

@app.post("/register")
def register(creds: UserCredentials):
    password_hash = hash_password(creds.password)
    try:
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (creds.username, password_hash))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username already exists")
    return {"message": "User registered successfully"}

@app.post("/login")
def login(creds: UserCredentials):
    cursor.execute("SELECT password_hash FROM users WHERE username = ?", (creds.username,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    stored_hash = row[0]
    if stored_hash != hash_password(creds.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = secrets.token_hex(32)
    tokens[token] = creds.username
    return {"token": token}

@app.get("/me")
def me(username: str = Depends(verify_token)):
    return {"username": username}