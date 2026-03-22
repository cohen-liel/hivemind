import pytest
from httpx import AsyncClient
from fastapi import status
from main import app
import database
import os

@pytest.fixture(scope="module", autouse=True)
def setup_and_teardown_db():
    # Setup: remove existing DB and init fresh
    if os.path.exists(database.DB_PATH):
        os.remove(database.DB_PATH)
    database.init_db()
    yield
    # Teardown: remove DB file
    if os.path.exists(database.DB_PATH):
        os.remove(database.DB_PATH)

@pytest.mark.anyio
async def test_create_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as client:
        data = {
            "url": "https://example.com",
            "title": "Example",
            "tags": ["tag1", "tag2"]
        }
        response = await client.post("/bookmarks", json=data)
        assert response.status_code == status.HTTP_200_OK
        json_data = response.json()
        assert json_data["url"] == data["url"]
        assert json_data["title"] == data["title"]
        assert set(json_data["tags"]) == set(data["tags"])
        assert "id" in json_data

@pytest.mark.anyio
async def test_get_bookmarks():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Create two bookmarks
        b1 = {
            "url": "https://site1.com",
            "title": "Site 1",
            "tags": ["news", "tech"]
        }
        b2 = {
            "url": "https://site2.com",
            "title": "Site 2",
            "tags": ["news", "sports"]
        }
        await client.post("/bookmarks", json=b1)
        await client.post("/bookmarks", json=b2)

        # Get all bookmarks
        response = await client.get("/bookmarks")
        assert response.status_code == status.HTTP_200_OK
        bookmarks = response.json()
        assert isinstance(bookmarks, list)
        assert any(b["url"] == b1["url"] for b in bookmarks)
        assert any(b["url"] == b2["url"] for b in bookmarks)

        # Filter by tag "tech"
        response = await client.get("/bookmarks", params={"tag": "tech"})
        assert response.status_code == status.HTTP_200_OK
        filtered = response.json()
        assert all("tech" in b["tags"] for b in filtered)
        assert any(b["url"] == b1["url"] for b in filtered)
        assert all(b["url"] != b2["url"] for b in filtered)

@pytest.mark.anyio
async def test_search_bookmarks():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Create bookmarks
        bm1 = {
            "url": "https://fastapi.tiangolo.com",
            "title": "FastAPI",
            "tags": ["python", "api"]
        }
        bm2 = {
            "url": "https://docs.python.org",
            "title": "Python Docs",
            "tags": ["python", "docs"]
        }
        await client.post("/bookmarks", json=bm1)
        await client.post("/bookmarks", json=bm2)

        # Search by "python"
        response = await client.get("/bookmarks/search", params={"q": "python"})
        assert response.status_code == status.HTTP_200_OK
        results = response.json()
        assert any("python" in tag for b in results for tag in b["tags"])
        assert any(b["url"] == bm1["url"] for b in results)
        assert any(b["url"] == bm2["url"] for b in results)

        # Search by "fastapi"
        response = await client.get("/bookmarks/search", params={"q": "fastapi"})
        assert response.status_code == status.HTTP_200_OK
        results = response.json()
        assert any(b["url"] == bm1["url"] for b in results)
        assert all(b["url"] != bm2["url"] for b in results)

@pytest.mark.anyio
async def test_delete_bookmark():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Create bookmark
        bm = {
            "url": "https://delete.me",
            "title": "Delete Me",
            "tags": ["temp"]
        }
        create_resp = await client.post("/bookmarks", json=bm)
        assert create_resp.status_code == status.HTTP_200_OK
        bookmark_id = create_resp.json()["id"]

        # Delete bookmark
        delete_resp = await client.delete(f"/bookmarks/{bookmark_id}")
        assert delete_resp.status_code == status.HTTP_204_NO_CONTENT

        # Confirm deletion by trying to delete again (should 404)
        delete_resp2 = await client.delete(f"/bookmarks/{bookmark_id}")
        assert delete_resp2.status_code == status.HTTP_404_NOT_FOUND
        assert "detail" in delete_resp2.json()

        # Confirm bookmark no longer in list
        get_resp = await client.get("/bookmarks")
        assert all(b["id"] != bookmark_id for b in get_resp.json())