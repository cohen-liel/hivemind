from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)


class AgentResponse(BaseModel):
    id: int
    name: str
    role: str
    created_at: datetime


class MessageCreateRequest(BaseModel):
    sender_id: int
    receiver_id: int
    content: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    id: int
    sender_id: int
    receiver_id: int
    content: str
    timestamp: datetime
