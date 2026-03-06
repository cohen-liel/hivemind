import os
import pytest
from pathlib import Path
from conversation_store import ConversationStore

# Simple test to verify ConversationStore functionality
@pytest.fixture
def store(tmp_path):
    s = ConversationStore()
    s.store_dir = tmp_path
    return s

def test_save_and_load_project(store):
    store.save_project("test_proj", {"name": "Test", "status": "active"})
    data = store.load_project("test_proj")
    assert data is not None
    assert data["name"] == "Test"
    assert data["status"] == "active"
    assert "updated_at" in data

def test_add_message(store):
    store.add_message("proj1", "architect", "Architect", "Hello", 0.01)
    data = store.load_project("proj1")
    assert data is not None
    assert len(data["messages"]) == 1
    assert data["messages"][0]["agent_name"] == "architect"
    assert data["messages"][0]["content"] == "Hello"
    assert data["messages"][0]["cost_usd"] == 0.01

def test_list_projects(store):
    store.save_project("proj1", {"project_id": "proj1", "name": "Project 1"})
    store.save_project("proj2", {"project_id": "proj2", "name": "Project 2"})
    projects = store.list_projects()
    assert len(projects) == 2
    names = [p["name"] for p in projects]
    assert "Project 1" in names and "Project 2" in names
