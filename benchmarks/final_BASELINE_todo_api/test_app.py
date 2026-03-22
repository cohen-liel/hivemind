import pytest
import asyncio
from httpx import AsyncClient
from fastapi import FastAPI
from fastapi.testclient import TestClient
from main import app
from database import get_connection

@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS todos")
    cursor.execute("""
        CREATE TABLE todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            completed BOOLEAN NOT NULL CHECK (completed IN (0,1))
        )
    """)
    conn.commit()
    conn.close()

@pytest.mark.asyncio
async def test_create_todo():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post("/todos/", json={"title": "Test Todo", "description": "Test Desc", "completed": False})
    assert response.status_code == 201
    data = response.json()
    assert data["id"] > 0
    assert data["title"] == "Test Todo"
    assert data["description"] == "Test Desc"
    assert data["completed"] is False

@pytest.mark.asyncio
async def test_read_todos_empty_then_one():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Initially one todo from previous test
        response = await ac.get("/todos/")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "Test Todo"

@pytest.mark.asyncio
async def test_read_todo_by_id():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/todos/1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["title"] == "Test Todo"

@pytest.mark.asyncio
async def test_read_todo_not_found():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/todos/999")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_update_todo():
    update_payload = {"title": "Updated Todo", "description": "Updated Desc", "completed": True}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.put("/todos/1", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["title"] == "Updated Todo"
    assert data["description"] == "Updated Desc"
    assert data["completed"] is True

@pytest.mark.asyncio
async def test_update_todo_not_found():
    update_payload = {"title": "No Todo", "description": "No Desc", "completed": False}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.put("/todos/999", json=update_payload)
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_delete_todo():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.delete("/todos/1")
    assert response.status_code == 204
    # Confirm deletion
    response = await ac.get("/todos/1")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_delete_todo_not_found():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.delete("/todos/999")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_crud_sequence():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Create multiple todos
        todos = [
            {"title": "Todo 1", "description": "Desc 1", "completed": False},
            {"title": "Todo 2", "description": "Desc 2", "completed": True},
            {"title": "Todo 3", "description": "Desc 3", "completed": False},
        ]
        created_ids = []
        for todo in todos:
            resp = await ac.post("/todos/", json=todo)
            assert resp.status_code == 201
            created_ids.append(resp.json()["id"])

        # Read all todos
        resp = await ac.get("/todos/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

        # Update second todo
        update_data = {"title": "Todo 2 Updated", "description": "Desc 2 Updated", "completed": False}
        resp = await ac.put(f"/todos/{created_ids[1]}", json=update_data)
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["title"] == "Todo 2 Updated"
        assert updated["completed"] is False

        # Delete first todo
        resp = await ac.delete(f"/todos/{created_ids[0]}")
        assert resp.status_code == 204

        # Confirm deletion
        resp = await ac.get(f"/todos/{created_ids[0]}")
        assert resp.status_code == 404

        # Confirm remaining todos count
        resp = await ac.get("/todos/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = [todo["id"] for todo in data]
        assert created_ids[0] not in ids
        assert created_ids[1] in ids
        assert created_ids[2] in ids