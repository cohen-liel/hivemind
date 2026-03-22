import os
import pytest
from fastapi.testclient import TestClient
from main import app
import database

DB_FILE = 'chat.db'


@pytest.fixture(autouse=True)
def setup_and_teardown_db():
    # Remove existing DB file if any
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    # Initialize fresh DB
    database.init_db()
    yield
    # Cleanup after test
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)


client = TestClient(app)


def create_agent(name: str, role: str):
    response = client.post("/agents", json={"name": name, "role": role})
    return response


def test_create_agent_success():
    response = create_agent("AgentA", "Scout")
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "AgentA"
    assert data["role"] == "Scout"
    assert "id" in data
    assert "created_at" in data


def test_create_agent_duplicate_name():
    response1 = create_agent("AgentB", "Spy")
    assert response1.status_code == 201
    response2 = create_agent("AgentB", "Spy")
    assert response2.status_code == 409
    assert "already exists" in response2.json().get("detail", "")


def test_list_agents_empty_and_nonempty():
    # Initially empty
    response = client.get("/agents")
    assert response.status_code == 200
    assert response.json() == []

    # Add agents
    create_agent("AgentC", "Scout")
    create_agent("AgentD", "Spy")

    response = client.get("/agents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    names = {agent["name"] for agent in data}
    assert "AgentC" in names and "AgentD" in names


def test_get_single_agent_success_and_not_found():
    # Create agent
    resp = create_agent("AgentE", "Spy")
    agent_id = resp.json()["id"]

    # Get existing agent
    response = client.get(f"/agents/{agent_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == agent_id
    assert data["name"] == "AgentE"

    # Get non-existent agent
    response = client.get("/agents/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent not found"


def test_send_message_success():
    # Create sender and receiver
    sender = create_agent("Sender1", "Scout").json()
    receiver = create_agent("Receiver1", "Spy").json()

    payload = {
        "sender_id": sender["id"],
        "receiver_id": receiver["id"],
        "content": "Hello Receiver1!"
    }
    response = client.post("/messages", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["sender_id"] == sender["id"]
    assert data["receiver_id"] == receiver["id"]
    assert data["content"] == "Hello Receiver1!"
    assert "id" in data
    assert "timestamp" in data


@pytest.mark.parametrize(
    "payload, missing_agent, missing_role",
    [
        ({"sender_id": 9999, "receiver_id": 1, "content": "Hi"}, "Sender", "sender"),
        ({"sender_id": 1, "receiver_id": 9999, "content": "Hi"}, "Receiver", "receiver"),
    ],
)
def test_send_message_to_nonexistent_agent(payload, missing_agent, missing_role):
    # Create one valid agent if needed
    if payload["sender_id"] == 1 or payload["receiver_id"] == 1:
        create_agent("ValidAgent", "Spy")

    response = client.post("/messages", json=payload)
    assert response.status_code == 404
    assert f"{missing_agent} agent not found" in response.json().get("detail", "")


def test_get_conversation_success_and_agent_not_found():
    # Create two agents
    agent1 = create_agent("Agent1", "Scout").json()
    agent2 = create_agent("Agent2", "Spy").json()

    # Send messages both ways
    client.post("/messages", json={
        "sender_id": agent1["id"],
        "receiver_id": agent2["id"],
        "content": "Hello from Agent1"
    })
    client.post("/messages", json={
        "sender_id": agent2["id"],
        "receiver_id": agent1["id"],
        "content": "Reply from Agent2"
    })

    # Valid conversation fetch
    response = client.get(f"/messages?sender_id={agent1['id']}&receiver_id={agent2['id']}")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    contents = [msg["content"] for msg in data]
    assert "Hello from Agent1" in contents
    assert "Reply from Agent2" in contents

    # Sender not found
    response = client.get(f"/messages?sender_id=9999&receiver_id={agent2['id']}")
    assert response.status_code == 404
    assert "Sender agent not found" in response.json().get("detail", "")

    # Receiver not found
    response = client.get(f"/messages?sender_id={agent1['id']}&receiver_id=9999")
    assert response.status_code == 404
    assert "Receiver agent not found" in response.json().get("detail", "")


def test_get_agent_messages_success_and_not_found():
    # Create agents
    agent1 = create_agent("AgentX", "Scout").json()
    agent2 = create_agent("AgentY", "Spy").json()

    # Send messages
    client.post("/messages", json={
        "sender_id": agent1["id"],
        "receiver_id": agent2["id"],
        "content": "Msg1"
    })
    client.post("/messages", json={
        "sender_id": agent2["id"],
        "receiver_id": agent1["id"],
        "content": "Msg2"
    })

    # Get messages for agent1
    response = client.get(f"/agents/{agent1['id']}/messages")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    contents = [msg["content"] for msg in data]
    assert "Msg1" in contents and "Msg2" in contents

    # Get messages for non-existent agent
    response = client.get("/agents/9999/messages")
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent not found"


def test_create_agent_bad_request_missing_fields():
    # Missing name
    response = client.post("/agents", json={"role": "Spy"})
    assert response.status_code == 422  # Validation error

    # Missing role
    response = client.post("/agents", json={"name": "AgentZ"})
    assert response.status_code == 422


def test_send_message_bad_request_missing_fields():
    # Create two agents
    agent1 = create_agent("AgentM", "Scout").json()
    agent2 = create_agent("AgentN", "Spy").json()

    # Missing content
    response = client.post("/messages", json={
        "sender_id": agent1["id"],
        "receiver_id": agent2["id"]
    })
    assert response.status_code == 422

    # Missing sender_id
    response = client.post("/messages", json={
        "receiver_id": agent2["id"],
        "content": "Hello"
    })
    assert response.status_code == 422

    # Missing receiver_id
    response = client.post("/messages", json={
        "sender_id": agent1["id"],
        "content": "Hello"
    })
    assert response.status_code == 422


def test_get_conversation_missing_query_params():
    # Missing sender_id
    response = client.get("/messages?receiver_id=1")
    assert response.status_code == 422

    # Missing receiver_id
    response = client.get("/messages?sender_id=1")
    assert response.status_code == 422


def test_get_agent_invalid_id():
    # Non-integer agent_id
    response = client.get("/agents/abc")
    assert response.status_code == 422

    # Non-integer agent_id for messages
    response = client.get("/agents/abc/messages")
    assert response.status_code == 422


def test_send_message_invalid_foreign_keys():
    # This is covered by 404 checks, but test direct DB integrity error fallback
    # Since the API checks existence first, this is hard to trigger via API.
    # So no direct test here; covered by existing tests.


def test_list_agents_ordering():
    # Create multiple agents
    create_agent("Agent1", "Role1")
    create_agent("Agent2", "Role2")
    create_agent("Agent3", "Role3")

    response = client.get("/agents")
    assert response.status_code == 200
    data = response.json()
    ids = [agent["id"] for agent in data]
    assert ids == sorted(ids)  # Should be ordered by id ascending


def test_get_agent_messages_ordering():
    # Create agents
    a1 = create_agent("A1", "R1").json()
    a2 = create_agent("A2", "R2").json()

    # Send messages in order
    client.post("/messages", json={"sender_id": a1["id"], "receiver_id": a2["id"], "content": "First"})
    client.post("/messages", json={"sender_id": a2["id"], "receiver_id": a1["id"], "content": "Second"})
    client.post("/messages", json={"sender_id": a1["id"], "receiver_id": a2["id"], "content": "Third"})

    response = client.get(f"/agents/{a1['id']}/messages")
    assert response.status_code == 200
    data = response.json()
    timestamps = [msg["timestamp"] for msg in data]
    assert timestamps == sorted(timestamps)  # Ascending order


def test_get_conversation_ordering():
    a1 = create_agent("AgentA", "RoleA").json()
    a2 = create_agent("AgentB", "RoleB").json()

    client.post("/messages", json={"sender_id": a1["id"], "receiver_id": a2["id"], "content": "Msg1"})
    client.post("/messages", json={"sender_id": a2["id"], "receiver_id": a1["id"], "content": "Msg2"})
    client.post("/messages", json={"sender_id": a1["id"], "receiver_id": a2["id"], "content": "Msg3"})

    response = client.get(f"/messages?sender_id={a1['id']}&receiver_id={a2['id']}")
    assert response.status_code == 200
    data = response.json()
    timestamps = [msg["timestamp"] for msg in data]
    assert timestamps == sorted(timestamps)


def test_create_agent_name_uniqueness_case_sensitive():
    # Create agent with name "AgentX"
    r1 = create_agent("AgentX", "RoleX")
    assert r1.status_code == 201

    # Create agent with name "agentx" (lowercase)
    r2 = create_agent("agentx", "RoleY")
    # Depending on SQLite collation, this may be allowed or not.
    # Default SQLite is case-sensitive for UNIQUE constraints.
    # So expect success here.
    assert r2.status_code == 201


def test_create_agent_name_empty_string():
    response = create_agent("", "RoleEmpty")
    # Pydantic should reject empty string for name (required str)
    assert response.status_code == 422


def test_create_agent_role_empty_string():
    response = create_agent("AgentEmptyRole", "")
    # Pydantic should reject empty string for role (required str)
    assert response.status_code == 422
