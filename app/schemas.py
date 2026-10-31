"""The API's response shapes, declared.

This module is the backend's single source of truth for every JSON shape the
API returns. Each route declares `response_model=...` from here, which makes
FastAPI's generated OpenAPI spec complete and authoritative.

Why this matters
----------------
Before this module existed, every route returned a bare dict and declared no
`response_model`. FastAPI's spec therefore described only the three request
bodies and none of the response shapes — so the frontend's `types.ts` was
hand-written from prose, and nothing mechanically checked that the two sides
agreed. The two could drift silently.

Now the spec describes every response, `scripts/export-openapi.py` writes it
to `openapi.json`, and `scripts/generate-frontend-types.py` derives the
frontend's TypeScript types from it. CI fails if either is stale.

The shapes here mirror `aidlc-docs/inception/api-contract.md`. If the two
disagree, the contract document is what must change — and the CI check that
compares the spec against the contract is what will say so.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
class UserOut(BaseModel):
    """The public view of a user. Never exposes `password_hash`."""

    id: str
    email: str
    name: str | None = None


class AuthResponse(BaseModel):
    access_token: str
    user: UserOut


class HealthOut(BaseModel):
    status: str


# --------------------------------------------------------------------------
# Chats
# --------------------------------------------------------------------------
class ChatSummary(BaseModel):
    id: str
    title: str
    preview: str
    updated_at: str


class AttachmentOut(BaseModel):
    filename: str
    size: int


class MessageOut(BaseModel):
    id: str
    # The database column is a free-form string, but every message the API
    # writes has exactly one of these two roles. Declaring the constraint
    # here is what lets the frontend's generated type narrow it to a literal
    # union — without it, the frontend would have to cast.
    role: Literal["user", "assistant"]
    content: str
    attachment: AttachmentOut | None = None
    created_at: str


class ChatDetail(BaseModel):
    id: str
    title: str
    messages: list[MessageOut]


# --------------------------------------------------------------------------
# Laws (e-Gov)
# --------------------------------------------------------------------------
class LawParagraph(BaseModel):
    num: str
    text: str


class LawArticle(BaseModel):
    law_id: str
    law_title: str
    article_num: str
    article_title: str
    paragraphs: list[LawParagraph]
    source_url: str


# --------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------
class ApiKeyOut(BaseModel):
    id: str
    prefix: str
    created_at: str
    # Always present — the key has simply never been used. These are declared
    # required-but-nullable (deliberately no `= None` default): Pydantic only
    # lists a field in the OpenAPI `required` array when it has no default, so
    # a default would make the field optional in the generated TypeScript and
    # the frontend would have to handle "absent" as a third case. The API
    # always sends both keys, so the contract says exactly that.
    last_used_at: str | None
    revoked_at: str | None


class ApiKeyCreated(BaseModel):
    """The one and only time the secret itself is returned."""

    id: str
    prefix: str
    key: str
    created_at: str


# --------------------------------------------------------------------------
# Streaming
#
# The stream is NDJSON — one JSON object per line — not a JSON array, so
# FastAPI cannot describe it as a response model. The event shapes are
# declared here anyway, and `scripts/export-openapi.py` folds them into the
# spec under the `x-stream-events` extension so the frontend's generator can
# read them from one place.
# --------------------------------------------------------------------------
class StreamCitation(BaseModel):
    id: str
    label: str
    law_id: str
    law_title: str
    article: str


class StreamEventChat(BaseModel):
    type: Literal["chat"]
    chat_id: str
    title: str


class StreamEventStage(BaseModel):
    type: Literal["stage"]
    stage: str
    status: Literal["active", "complete"]
    label: str | None = None


class StreamEventToolCall(BaseModel):
    type: Literal["tool_call"]
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class StreamEventToolResult(BaseModel):
    type: Literal["tool_result"]
    id: str
    name: str
    summary: str


class StreamEventTextDelta(BaseModel):
    type: Literal["text_delta"]
    delta: str


class StreamEventCitations(BaseModel):
    type: Literal["citations"]
    items: list[StreamCitation]


class StreamEventDone(BaseModel):
    type: Literal["done"]
    message_id: str


class StreamEventError(BaseModel):
    type: Literal["error"]
    message: str


#: Every event the stream can emit, in the order the agent emits them.
#: `scripts/export-openapi.py` serialises this list into the spec.
STREAM_EVENT_MODELS: tuple[type[BaseModel], ...] = (
    StreamEventChat,
    StreamEventStage,
    StreamEventToolCall,
    StreamEventToolResult,
    StreamEventTextDelta,
    StreamEventCitations,
    StreamEventDone,
    StreamEventError,
)

#: The models the stream events are built from. These are referenced by the
#: events but are not themselves events, so they are listed separately and
#: serialised alongside the events in the spec's `x-stream-events` extension.
STREAM_MODELS: tuple[type[BaseModel], ...] = (StreamCitation,)
