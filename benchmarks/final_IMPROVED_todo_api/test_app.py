import pytest
from httpx import AsyncClient
from fastapi import status
from main import app
import database

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    database.DB_PATH = ":memory:"
    database.init_db()
    yield
    # No teardown needed for in-memory DB

@pytest.mark.anyio
async def test_crud_operations():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Create TODO
        todo_data = {"title": "Test TODO", "description": "Test description"}
        response = await client.post("/todos", json=todo_data)
        assert response.status_code == status.HTTP_200_OK
        todo = response.json()
        assert todo["title"] == todo_data["title"]
        assert todo["description"] == todo_data["description"]
        todo_id = todo["id"]

        # Read all TODOs
        response = await client.get("/todos")
        assert response.status_code == status.HTTP_200_OK
        todos = response.json()
        assert any(t["id"] == todo_id for t in todos)

        # Read single TODO
        response = await client.get(f"/todos/{todo_id}")
        assert response.status_code == status.HTTP_200_OK
        todo_single = response.json()
        assert todo_single["id"] == todo_id
        assert todo_single["title"] == todo_data["title"]

        # Update TODO - partial update (title only)
        update_data = {"title": "Updated Title"}
        response = await client.put(f"/todos/{todo_id}", json=update_data)
        assert response.status_code == status.HTTP_200_OK
        updated_todo = response.json()
        assert updated_todo["title"] == update_data["title"]
        assert updated_todo["description"] == todo_data["description"]

        # Update TODO - partial update (description only)
        update_data = {"description": "Updated Description"}
        response = await client.put(f"/todos/{todo_id}", json=update_data)
        assert response.status_code == status.HTTP_200_OK
        updated_todo = response.json()
        assert updated_todo["description"] == update_data["description"]

        # Update TODO - partial update (done only)
        update_data = {"done": True}
        response = await client.put(f"/todos/{todo_id}", json=update_data)
        assert response.status_code == status.HTTP_200_OK
        updated_todo = response.json()
        assert updated_todo["done"] is True

        # Update TODO - full update
        update_data = {"title": "Final Title", "description": "Final Desc", "done": False}
        response = await client.put(f"/todos/{todo_id}", json=update_data)
        assert response.status_code == status.HTTP_200_OK
        updated_todo = response.json()
        assert updated_todo["title"] == update_data["title"]
        assert updated_todo["description"] == update_data["description"]
        assert updated_todo["done"] is False

        # Delete TODO
        response = await client.delete(f"/todos/{todo_id}")
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Confirm deletion
        response = await client.get(f"/todos/{todo_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND

@pytest.mark.anyio
async def test_not_found_and_error_cases():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Get non-existent TODO
        response = await client.get("/todos/9999")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Update non-existent TODO
        update_data = {"title": "Nope"}
        response = await client.put("/todos/9999", json=update_data)
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # Delete non-existent TODO
        response = await client.delete("/todos/9999")
        assert response.status_code == status.HTTP_404_NOT_FOUND

@pytest.mark.anyio
async def test_create_todo_missing_title():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Missing title field
        todo_data = {"description": "No title"}
        response = await client.post("/todos", json=todo_data)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

@pytest.mark.anyio
async def test_update_todo_no_fields():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Create TODO first
        todo_data = {"title": "Update test"}
        response = await client.post("/todos", json=todo_data)
        todo = response.json()
        todo_id = todo["id"]

        # Update with empty body
        response = await client.put(f"/todos/{todo_id}", json={})
        assert response.status_code == status.HTTP_200_OK
        updated_todo = response.json()
        assert updated_todo["title"] == todo_data["title"]

        # Cleanup
        await client.delete(f"/todos/{todo_id}")