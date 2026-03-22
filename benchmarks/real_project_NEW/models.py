from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


# Agent Models

class AgentBase(BaseModel):
    name: str = Field(..., example="Agent007")
    role: str = Field(..., example="Spy")


class AgentCreate(AgentBase):
    pass


class AgentResponse(AgentBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True


# Message Models

class MessageBase(BaseModel):
    sender_id: int = Field(..., example=1)
    receiver_id: int = Field(..., example=2)
    content: str = Field(..., example="Hello there!")


class MessageCreate(MessageBase):
    pass


class MessageResponse(MessageBase):
    id: int
    timestamp: datetime

    class Config:
        orm_mode = True


# Query Parameters for GET /messages?sender_id=X&receiver_id=Y

class ConversationQueryParams(BaseModel):
    sender_id: int = Field(..., example=1)
    receiver_id: int = Field(..., example=2)
