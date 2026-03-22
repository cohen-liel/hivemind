from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel, Field, ConfigDict
import hashlib
import secrets
from typing import Optional
from database import init_db, create_user, get_user, get_user_by_id

app = FastAPI()

init_db()

class UserRegister(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class UserLogin(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class User(BaseModel):
    id: int
    username: str

    model_config = ConfigDict(from_attributes=True)

tokens = {}

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash

def get_current_user(authorization: Optional[str] = Header(None)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization[7:]
    user_id = tokens.get(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_row = get_user_by_id(user_id)
    if not user_row:
        raise HTTPException(status_code=401, detail="User not found")
    return User(id=user_row[0], username=user_row[1])

@app.post("/register")
def register(user: UserRegister):
    existing_user = get_user(user.username)
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    password_hash = hash_password(user.password)
    user_id = create_user(user.username, password_hash)
    return {"id": user_id, "username": user.username}

@app.post("/login")
def login(user: UserLogin):
    user_row = get_user(user.username)
    if not user_row or not verify_password(user.password, user_row[2]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = secrets.token_hex(32)
    tokens[token] = user_row[0]
    return {"token": token}

@app.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return current_user