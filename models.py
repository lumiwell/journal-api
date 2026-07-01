from sqlmodel import SQLModel, Field
from typing import Optional
import uuid
from datetime import datetime, timezone

class User(SQLModel, table=True):
    __tablename__ = "users"  # type: ignore
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"  # type: ignore
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class Message(SQLModel, table=True):
    __tablename__ = "messages"  # type: ignore
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(foreign_key="chat_sessions.id", index=True)
    diary_id: Optional[uuid.UUID] = Field(default=None, foreign_key="diaries.id", index=True)
    role: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class Diary(SQLModel, table=True):
    __tablename__ = "diaries"  # type: ignore
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(foreign_key="chat_sessions.id", index=True)
    core_emotion: str
    insight: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

