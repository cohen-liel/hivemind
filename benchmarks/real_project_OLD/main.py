from fastapi import FastAPI, HTTPException, Query, status
from typing import List, Optional
from database import init_db, get_connection
from models import (
    AgentCreateRequest,
    AgentResponse,
    MessageCreateRequest,
    MessageResponse,
)
import sqlite3

app = FastAPI()


@app.on_event("startup")
def startup_event():
    init_db()


# Helper functions to convert sqlite3.Row to Pydantic models
def row_to_agent(row: sqlite3.Row) -> AgentResponse:
    return AgentResponse(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        created_at=row["created_at"],
    )


def row_to_message(row: sqlite3.Row) -> MessageResponse:
    return MessageResponse(
        id=row["id"],
        sender_id=row["sender_id"],
        receiver_id=row["receiver_id"],
        content=row["content"],
        timestamp=row["timestamp"],
    )


@app.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(agent_req: AgentCreateRequest):
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO agents (name, role) VALUES (?, ?)",
                (agent_req.name, agent_req.role),
            )
            conn.commit()
            agent_id = cursor.lastrowid
            cursor.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
            row = cursor.fetchone()
            return row_to_agent(row)
        except sqlite3.IntegrityError as e:
            # Likely UNIQUE constraint failed on name
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Agent with this name already exists.",
            )


@app.get("/agents", response_model=List[AgentResponse])
def list_agents():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents ORDER BY id")
        rows = cursor.fetchall()
        return [row_to_agent(row) for row in rows]


@app.get("/agents/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        return row_to_agent(row)


@app.post("/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def send_message(msg_req: MessageCreateRequest):
    with get_connection() as conn:
        cursor = conn.cursor()
        # Check sender exists
        cursor.execute("SELECT id FROM agents WHERE id = ?", (msg_req.sender_id,))
        if not cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sender agent not found",
            )
        # Check receiver exists
        cursor.execute("SELECT id FROM agents WHERE id = ?", (msg_req.receiver_id,))
        if not cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Receiver agent not found",
            )
        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
            (msg_req.sender_id, msg_req.receiver_id, msg_req.content),
        )
        conn.commit()
        message_id = cursor.lastrowid
        cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        return row_to_message(row)


@app.get("/messages", response_model=List[MessageResponse])
def get_conversation(
    sender_id: int = Query(..., description="Sender agent ID"),
    receiver_id: int = Query(..., description="Receiver agent ID"),
):
    with get_connection() as conn:
        cursor = conn.cursor()
        # Validate both agents exist
        cursor.execute("SELECT id FROM agents WHERE id = ?", (sender_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender agent not found")
        cursor.execute("SELECT id FROM agents WHERE id = ?", (receiver_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiver agent not found")

        # Get messages where (sender=sender_id AND receiver=receiver_id) OR (sender=receiver_id AND receiver=sender_id)
        cursor.execute(
            """
            SELECT * FROM messages
            WHERE (sender_id = ? AND receiver_id = ?)
               OR (sender_id = ? AND receiver_id = ?)
            ORDER BY timestamp ASC
            """,
            (sender_id, receiver_id, receiver_id, sender_id),
        )
        rows = cursor.fetchall()
        return [row_to_message(row) for row in rows]


@app.get("/agents/{agent_id}/messages", response_model=List[MessageResponse])
def get_agent_messages(agent_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        # Validate agent exists
        cursor.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

        cursor.execute(
            """
            SELECT * FROM messages
            WHERE sender_id = ? OR receiver_id = ?
            ORDER BY timestamp ASC
            """,
            (agent_id, agent_id),
        )
        rows = cursor.fetchall()
        return [row_to_message(row) for row in rows]
