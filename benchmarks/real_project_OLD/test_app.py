import os
import pytest
import sqlite3
from fastapi.testclient import TestClient
from main import app
from database import init_db, DB_FILE

@pytest.fixture(autouse=True)
def setup_and_teardown_db():
    # Remove existing DB file if any
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    # Initialize fresh DB
    init_db()
    yield
    # Cleanup after test
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

client = TestClient(app)

def create_agent(name: str, role: str):
    response = client.post("/agents", json={"name": name, "role": role})
    return response

def create_message(sender_id: int, receiver_id: int, content: str):
    response = client.post("/messages", json={
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "content": content
    })
    return response

def test_agent_creation_success():
    response = create_agent("Alice", "Support")
    assert response.status_code == 201
    data = response.json()
    assert data["id"] > 0
    assert data["name"] == "Alice"
    assert data["role"] == "Support"
    assert "created_at" in data

def test_agent_creation_duplicate_name():
    r1 = create_agent("Bob", "Sales")
    assert r1.status_code == 201
    r2 = create_agent("Bob", "Marketing")
    # Could be 400 or 409 depending on implementation
    assert r2.status_code in (400, 409)

def test_list_agents_empty_and_nonempty():
    # Initially empty
    response = client.get("/agents")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 0

    # Add agents
    create_agent("Agent1", "Role1")
    create_agent("Agent2", "Role2")

    response = client.get("/agents")
    assert response.status_code == 200
    agents = response.json()
    assert isinstance(agents, list)
    assert len(agents) == 2
    names = {a["name"] for a in agents}
    assert "Agent1" in names and "Agent2" in names

def test_get_single_agent_found_and_not_found():
    r = create_agent("Charlie", "Dev")
    agent_id = r.json()["id"]

    # Found
    response = client.get(f"/agents/{agent_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == agent_id
    assert data["name"] == "Charlie"

    # Not found
    response = client.get("/agents/999999")
    assert response.status_code == 404

def test_send_message_success():
    r1 = create_agent("Sender", "RoleA")
    r2 = create_agent("Receiver", "RoleB")
    sender_id = r1.json()["id"]
    receiver_id = r2.json()["id"]

    response = create_message(sender_id, receiver_id, "Hello there!")
    assert response.status_code == 201
    msg = response.json()
    assert msg["id"] > 0
    assert msg["sender_id"] == sender_id
    assert msg["receiver_id"] == receiver_id
    assert msg["content"] == "Hello there!"
    assert "timestamp" in msg

def test_send_message_to_nonexistent_agent():
    r = create_agent("OnlyAgent", "RoleX")
    sender_id = r.json()["id"]
    non_existent_id = 999999

    # Non-existent receiver
    response = create_message(sender_id, non_existent_id, "Hi")
    assert response.status_code in (400, 404)

    # Non-existent sender
    response = create_message(non_existent_id, sender_id, "Hi")
    assert response.status_code in (400, 404)

def test_get_conversation_between_agents():
    r1 = create_agent("AgentA", "RoleA")
    r2 = create_agent("AgentB", "RoleB")
    a_id = r1.json()["id"]
    b_id = r2.json()["id"]

    # Send messages both ways
    create_message(a_id, b_id, "Msg 1 from A to B")
    create_message(b_id, a_id, "Reply from B to A")
    create_message(a_id, b_id, "Msg 2 from A to B")

    # Get conversation A->B
    response = client.get(f"/messages?sender_id={a_id}&receiver_id={b_id}")
    assert response.status_code == 200
    msgs = response.json()
    assert isinstance(msgs, list)
    assert len(msgs) == 2
    contents = [m["content"] for m in msgs]
    assert "Msg 1 from A to B" in contents
    assert "Msg 2 from A to B" in contents

    # Get conversation B->A
    response = client.get(f"/messages?sender_id={b_id}&receiver_id={a_id}")
    assert response.status_code == 200
    msgs = response.json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "Reply from B to A"

def test_get_agent_messages():
    r1 = create_agent("AgentX", "RoleX")
    r2 = create_agent("AgentY", "RoleY")
    r3 = create_agent("AgentZ", "RoleZ")
    x_id = r1.json()["id"]
    y_id = r2.json()["id"]
    z_id = r3.json()["id"]

    # Messages involving AgentX
    create_message(x_id, y_id, "X->Y message")
    create_message(y_id, x_id, "Y->X message")
    create_message(z_id, x_id, "Z->X message")
    create_message(y_id, z_id, "Y->Z message")  # Not involving X

    response = client.get(f"/agents/{x_id}/messages")
    assert response.status_code == 200
    msgs = response.json()
    assert isinstance(msgs, list)
    # Should include all messages where AgentX is sender or receiver
    contents = {m["content"] for m in msgs}
    assert "X->Y message" in contents
    assert "Y->X message" in contents
    assert "Z->X message" in contents
    assert "Y->Z message" not in contents

def test_post_agents_bad_request():
    # Missing name
    response = client.post("/agents", json={"role": "Role"})
    assert response.status_code == 422  # validation error

    # Empty name
    response = client.post("/agents", json={"name": "", "role": "Role"})
    assert response.status_code == 422

    # Missing role
    response = client.post("/agents", json={"name": "Name"})
    assert response.status_code == 422

def test_post_messages_bad_request():
    r1 = create_agent("SenderX", "RoleX")
    r2 = create_agent("ReceiverY", "RoleY")
    sender_id = r1.json()["id"]
    receiver_id = r2.json()["id"]

    # Missing fields
    response = client.post("/messages", json={"sender_id": sender_id, "content": "Hi"})
    assert response.status_code == 422

    # Empty content
    response = client.post("/messages", json={"sender_id": sender_id, "receiver_id": receiver_id, "content": ""})
    assert response.status_code == 422

def test_get_agents_messages_not_found():
    # Non-existent agent
    response = client.get("/agents/999999/messages")
    # Depending on implementation, could be 200 with empty list or 404
    # We accept both but prefer 404 if agent not found
    assert response.status_code in (200, 404)

def test_get_messages_query_params_missing_or_invalid():
    # Missing sender_id or receiver_id
    response = client.get("/messages")
    # Should return 422 because required query params missing
    assert response.status_code == 422

    # Invalid sender_id type
    response = client.get("/messages?sender_id=abc&receiver_id=1")
    assert response.status_code == 422

    # Invalid receiver_id type
    response = client.get("/messages?sender_id=1&receiver_id=xyz")
    assert response.status_code == 422
