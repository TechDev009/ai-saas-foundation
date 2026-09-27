from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class UserRead(APIModel):
    id: str
    email: str
    display_name: str
    created_at: datetime


class DevTokenResponse(BaseModel):
    token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserRead
    user_id_header: str = "X-Dev-User-Id"


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(
        default=None, min_length=1, max_length=140, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )


class WorkspaceRead(APIModel):
    id: str
    name: str
    slug: str
    created_at: datetime


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["member", "admin"] = "member"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class MembershipRead(APIModel):
    workspace_id: str
    user_id: str
    role: str
    created_at: datetime
    user: UserRead | None = None


class DocumentRead(APIModel):
    id: str
    workspace_id: str
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    status: str
    processing_error: str | None
    created_at: datetime
    processed_at: datetime | None


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    excerpt: str = Field(max_length=2000)
    score: float = Field(ge=0, le=1.0)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class SearchResponse(BaseModel):
    query: str
    results: list[Citation]


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class AnswerResponse(BaseModel):
    answer: str
    abstained: bool
    citations: list[Citation]
    conversation_id: str
    message_id: str
    request_id: str


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ConversationRead(APIModel):
    id: str
    workspace_id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageRead(APIModel):
    id: str
    conversation_id: str
    workspace_id: str
    role: str
    content: str
    citations: list[Citation] | None = None
    created_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class FeedbackCreate(BaseModel):
    rating: Literal["positive", "negative"]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackRead(APIModel):
    id: str
    workspace_id: str
    message_id: str
    rating: str
    comment: str | None
    created_at: datetime


class UsageRead(BaseModel):
    workspace_id: str
    period_start: datetime
    total_units: int
    by_kind: dict[str, int]


class AuditEventRead(APIModel):
    id: str
    workspace_id: str | None
    user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    metadata_json: dict[str, Any] | None
    request_id: str | None
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]
    service: str
    version: str
