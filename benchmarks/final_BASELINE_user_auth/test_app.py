import pytest
import asyncio
from httpx import AsyncClient
from main import app, conn, cursor, tokens

@pytest.fixture(autouse=True)
def clear_db_and_tokens():
    cursor.execute("DELETE FROM users")
    conn.commit()
    tokens.clear()
    yield
    cursor.execute("DELETE FROM users")
    conn.commit()
    tokens.clear()

@pytest.mark.asyncio
async def test_register_success():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post("/register", json={"username": "user1", "password": "pass1"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "User registered successfully"

@pytest.mark.asyncio
async def test_register_duplicate_username():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        await ac.post("/register", json={"username": "user1", "password": "pass1"})
        response = await ac.post("/register", json={"username": "user1", "password": "pass2"})
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Username already exists"

@pytest.mark.asyncio
async def test_login_success():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        await ac.post("/register", json={"username": "user1", "password": "pass1"})
        response = await ac.post("/login", json={"username": "user1", "password": "pass1"})
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert isinstance(data["token"], str)
    assert len(data["token"]) > 0

@pytest.mark.asyncio
async def test_login_wrong_password():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        await ac.post("/register", json={"username": "user1", "password": "pass1"})
        response = await ac.post("/login", json={"username": "user1", "password": "wrongpass"})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Invalid username or password"

@pytest.mark.asyncio
async def test_login_nonexistent_user():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post("/login", json={"username": "nouser", "password": "pass"})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Invalid username or password"

@pytest.mark.asyncio
async def test_protected_endpoint_success():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        await ac.post("/register", json={"username": "user1", "password": "pass1"})
        login_resp = await ac.post("/login", json={"username": "user1", "password": "pass1"})
        token = login_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = await ac.get("/protected", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "user1" in data["message"]

@pytest.mark.asyncio
async def test_protected_endpoint_missing_token():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/protected")
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data

@pytest.mark.asyncio
async def test_protected_endpoint_invalid_token():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        headers = {"Authorization": "Bearer invalidtoken"}
        response = await ac.get("/protected", headers=headers)
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Invalid or expired token"