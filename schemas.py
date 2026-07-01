from pydantic import BaseModel, EmailStr
from typing import Optional
import uuid

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    guest_session_id: Optional[uuid.UUID] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str
    guest_session_id: Optional[uuid.UUID] = None

class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr

class Token(BaseModel):
    access_token: str
    token_type: str
    session_id: Optional[str] = None
