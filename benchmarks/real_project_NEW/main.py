from fastapi import FastAPI, HTTPException, status, Query
from fastapi.responses import JSONResponse
from sqlite3 import IntegrityError
from typing import List, Optional

import database
import models

app = FastAPI(title="Agent-to-Agent Chat System")


@app.on_event("startup")
def startup_event():
    database.init_db()


# Agents Endpoints

@app.post("/agents", response_model=models.AgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(agent: models.AgentCreate):
    try:
        created_agent = database.create_agent(agent.name, agent.role)
        return created_agent
    except IntegrityError:
        # Likely UNIQUE constraint failed on name
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent with name '{agent.name}' already exists."
        )


@app.get("/agents", response_model=List[models.AgentResponse])
def list_agents():
    agents = database.get_agents()
    return agents


@app.get("/agents/{agent_id}", response_model=models.AgentResponse)
def get_agent(agent_id: int):
    agent = database.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


# Messages Endpoints

@app.post("/messages", response_model=models.MessageResponse, status_code=status.HTTP_201_CREATED)
def send_message(message: models.MessageCreate):
    # Validate sender and receiver exist
    sender = database.get_agent(message.sender_id)
    if not sender:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender agent not found")
    receiver = database.get_agent(message.receiver_id)
    if not receiver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiver agent not found")

    try:
        created_message = database.create_message(message.sender_id, message.receiver_id, message.content)
        return created_message
    except IntegrityError:
        # Foreign key constraint failed or other DB error
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message data")


@app.get("/messages", response_model=List[models.MessageResponse])
def get_conversation(sender_id: int = Query(..., description="Sender agent ID"), receiver_id: int = Query(..., description="Receiver agent ID")):
    # Validate agents exist
    sender = database.get_agent(sender_id)
    if not sender:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender agent not found")
    receiver = database.get_agent(receiver_id)
    if not receiver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiver agent not found")

    messages = database.get_conversation(sender_id, receiver_id)
    return messages


@app.get("/agents/{agent_id}/messages", response_model=List[models.MessageResponse])
def get_agent_messages(agent_id: int):
    agent = database.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    messages = database.get_agent_messages(agent_id)
    return messages
