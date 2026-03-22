import pytest
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from main import app
from database import DB_PATH
import os

@pytest.fixture(autouse=True, scope="module")
def setup_and_teardown_db():
    # Remove existing DB file if any
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    # Initialize DB
    from main import init_db
    init_db()
    yield
    # Cleanup DB file after tests
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

@pytest.mark.anyio
async def test_register_login_and_protected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Register user
        register_data = {"username": "testuser", "password": "testpass"}
        r = await client.post("/register", json=register_data)
        assert r.status_code == 200
        user_data = r.json()
        assert isinstance(user_data["id"], int)
        assert user_data["id"] > 0

        # Register same user again should fail
        r = await client.post("/register", json=register_data)
        assert r.status_code == 400
        assert "Username" in r.json()["detail"]

        # Login with correct credentials
        r = await client.post("/login", json=register_data)
        assert r.status_code == 200
        token = r.json().get("token")
        assert isinstance(token, str)
        assert len(token) > 0

        # Login with wrong password
        r = await client.post("/login", json={"username": "testuser", "password": "wrongpass"})
        assert r.status_code == 401
        assert "Invalid" in r.json()["detail"]

        # Login with non-existent user
        r = await client.post("/login", json={"username": "nouser", "password": "nopass"})
        assert r.status_code == 401
        assert "Invalid" in r.json()["detail"]

        # Access protected endpoint without token
        r = await client.get("/me")
        assert r.status_code == 401
        assert "Unauthorized" in r.json()["detail"]

        # Access protected endpoint with invalid token
        r = await client.get("/me", headers={"Authorization": "Bearer invalidtoken"})
        assert r.status_code == 401
        assert "token" in r.json()["detail"]

        # Access protected endpoint with valid token
        r = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        user = r.json()
        assert user["id"] == user_data["id"]
        assert user["username"] == "testuser"

@pytest.mark.anyio
async def test_register_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Empty username
        r = await client.post("/register", json={"username": "", "password": "pass"})
        assert r.status_code == 422

        # Empty password
        r = await client.post("/register", json={"username": "user2", "password": ""})
        assert r.status_code == 422

@pytest.mark.anyio
async def test_login_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Empty username
        r = await client.post("/login", json={"username": "", "password": "pass"})
        assert r.status_code == 422

        # Empty password
        r = await client.post("/login", json={"username": "user2", "password": ""})
        assert r.status_code == 422