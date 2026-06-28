"""Pydantic request/response schemas — extracted from server.py v29."""
from __future__ import annotations
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    file_context: str | None = None
    custom_prompt: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


class ModelCreateRequest(BaseModel):
    name: str
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096


class ModelUpdateRequest(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class KBRequest(BaseModel):
    name: str
    description: str = ""
    allowed_roles: list[str] | None = None


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None


class PasswordChangeRequest(BaseModel):
    new_password: str


class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    triggers: list[str] | None = None
    prompt: str = ""
    tools: list[str] | None = None


class TagRequest(BaseModel):
    tag: str


class PromptUpdateRequest(BaseModel):
    custom_prompt: str = ""
