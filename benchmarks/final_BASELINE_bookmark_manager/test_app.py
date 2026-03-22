import pytest
import asyncio
from httpx import AsyncClient
from main import app, init_db
from database import get_connection

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    # Initialize DB and clear bookmarks table before tests
    init_db()
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM bookmarks")
    conn.commit()
    conn.close()
    yield
    # Cleanup after tests
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM bookmarks")
    conn.commit()
    conn.close()

@pytest.mark.asyncio
async def test_create_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        data = {
            "url": "https://example.com",
            "title": "Example",
            "tags": ["test", "example"]
        }
        response = await ac.post("/bookmarks/", json=data)
    assert response.status_code == 201
    json_resp = response.json()
    assert json_resp["url"] == data["url"]
    assert json_resp["title"] == data["title"]
    assert set(json_resp["tags"]) == set(data["tags"])
    assert "id" in json_resp

@pytest.mark.asyncio
async def test_read_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Create bookmark first
        data = {
            "url": "https://readtest.com",
            "title": "Read Test",
            "tags": ["read"]
        }
        create_resp = await ac.post("/bookmarks/", json=data)
        bookmark_id = create_resp.json()["id"]

        # Read bookmark
        response = await ac.get(f"/bookmarks/{bookmark_id}")
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["id"] == bookmark_id
    assert json_resp["url"] == data["url"]
    assert json_resp["title"] == data["title"]
    assert set(json_resp["tags"]) == set(data["tags"])

@pytest.mark.asyncio
async def test_read_nonexistent_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/bookmarks/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}

@pytest.mark.asyncio
async def test_update_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Create bookmark first
        data = {
            "url": "https://updatetest.com",
            "title": "Update Test",
            "tags": ["update"]
        }
        create_resp = await ac.post("/bookmarks/", json=data)
        bookmark_id = create_resp.json()["id"]

        # Update bookmark
        update_data = {
            "url": "https://updated.com",
            "title": "Updated Title",
            "tags": ["updated", "test"]
        }
        response = await ac.put(f"/bookmarks/{bookmark_id}", json=update_data)
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["id"] == bookmark_id
    assert json_resp["url"] == update_data["url"]
    assert json_resp["title"] == update_data["title"]
    assert set(json_resp["tags"]) == set(update_data["tags"])

@pytest.mark.asyncio
async def test_update_nonexistent_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        update_data = {
            "url": "https://noexist.com",
            "title": "No Exist",
            "tags": []
        }
        response = await ac.put("/bookmarks/999999", json=update_data)
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}

@pytest.mark.asyncio
async def test_delete_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Create bookmark first
        data = {
            "url": "https://deletetest.com",
            "title": "Delete Test",
            "tags": ["delete"]
        }
        create_resp = await ac.post("/bookmarks/", json=data)
        bookmark_id = create_resp.json()["id"]

        # Delete bookmark
        response = await ac.delete(f"/bookmarks/{bookmark_id}")
    assert response.status_code == 204

    # Confirm deletion
    async with AsyncClient(app=app, base_url="http://test") as ac:
        get_resp = await ac.get(f"/bookmarks/{bookmark_id}")
    assert get_resp.status_code == 404

@pytest.mark.asyncio
async def test_delete_nonexistent_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.delete("/bookmarks/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}

@pytest.mark.asyncio
async def test_search_bookmarks():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Clear all bookmarks
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM bookmarks")
        conn.commit()
        conn.close()

        # Create multiple bookmarks
        bookmarks = [
            {"url": "https://fastapi.tiangolo.com", "title": "FastAPI", "tags": ["python", "api"]},
            {"url": "https://docs.python.org", "title": "Python Docs", "tags": ["python", "docs"]},
            {"url": "https://example.com", "title": "Example", "tags": ["test"]},
            {"url": "https://pytest.org", "title": "Pytest", "tags": ["python", "testing"]},
        ]
        for bm in bookmarks:
            await ac.post("/bookmarks/", json=bm)

        # Search by tag
        response = await ac.get("/bookmarks/search/", params={"tag": "python"})
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 3
        for bm in results:
            assert "python" in bm["tags"]

        # Search by title substring
        response = await ac.get("/bookmarks/search/", params={"query": "docs"})
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["title"].lower().find("docs") != -1

        # Search by tag and query
        response = await ac.get("/bookmarks/search/", params={"tag": "python", "query": "test"})
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert "python" in results[0]["tags"]
        assert "test" in results[0]["title"].lower()

        # Search with no filters returns all
        response = await ac.get("/bookmarks/search/")
        assert response.status_code == 200
        results = response.json()
        assert len(results) >= 4  # At least the ones we added

@pytest.mark.asyncio
async def test_create_bookmark_invalid_url():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        data = {
            "url": "not-a-url",
            "title": "Invalid URL",
            "tags": []
        }
        response = await ac.post("/bookmarks/", json=data)
    assert response.status_code == 422  # Validation error

@pytest.mark.asyncio
async def test_create_bookmark_missing_title():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        data = {
            "url": "https://valid.com"
        }
        response = await ac.post("/bookmarks/", json=data)
    assert response.status_code == 422  # Validation error