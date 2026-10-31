# Japanese Law Contract Analyzer - Backend

FastAPI + SQLAlchemy async (Postgres) + LangChain v1 agent on Gemini + e-Gov Law API v2 + MCP server. API contract: `../aidlc-docs/inception/api-contract.md`.

## Prerequisites

Python 3.12, [uv](https://docs.astral.sh/uv/), Docker. A Gemini API key (`GOOGLE_API_KEY`) is needed only for the LLM agent; everything else works without it.

## Setup

```bash
docker compose up -d        # Postgres 17 on host port 5434 (db/user/pass: legal)
uv sync
cp .env.example .env        # set GOOGLE_API_KEY (and optionally GEMINI_MODEL, JWT_SECRET)
uv run uvicorn app.main:app --reload --port 8000
```

Tables are created on startup. Smoke-test the e-Gov tools: `uv run python check_egov_tools.py`.

## Endpoints (all under `/api`, Bearer JWT except health/register/login)

- `GET /api/health`
- `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- `GET /api/chats`, `GET /api/chats/{id}`, `DELETE /api/chats/{id}`
- `POST /api/chats/stream` (multipart: `message`, optional `chat_id`, optional `file` .txt/.pdf <=5MB) -> NDJSON events
- `GET /api/laws/{law_id}/articles/{article_num}` (article_num `24` or `32_2` for 32条の2)
- `GET/POST /api/keys`, `DELETE /api/keys/{id}` (key `jlca_...` shown once, stored sha256-hashed)
- MCP: `/mcp` (streamable HTTP, `Authorization: Bearer jlca_...`), tools `analyze_contract_risk`, `search_japanese_law`

## Streaming example

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login -H 'content-type: application/json' \
  -d '{"email":"a@b.co","password":"pw12345"}' | jq -r .access_token)
curl -N -X POST localhost:8000/api/chats/stream -H "Authorization: Bearer $TOKEN" \
  -F message="Review this contract" -F file=@contract.txt
```

Event types: `chat`, `stage`, `tool_call`, `tool_result`, `text_delta`, `citations`, `done`, `error`.

## The OpenAPI spec — the API's source of truth

The backend owns the API, so the contract is generated from here:

```
app/routes.py + app/schemas.py  ──▶  openapi.json  ──▶  ../frontend/src/shared/api/types.ts
```

`openapi.json` is committed. Regenerate it after **any** change to a route, a
request body, or a response model:

```bash
uv run python scripts/export-openapi.py           # write openapi.json
uv run python scripts/export-openapi.py --check   # fail if it is stale (CI does this)
```

`app/schemas.py` holds every response model. Two conventions matter:

- Declare a field's true constraints. `role: Literal["user", "assistant"]`
  narrows the frontend's type to a literal union; `role: str` gives it `string`
  and forces a cast at the call site.
- A field that is always sent but sometimes `null` must be declared
  **without a default** (`last_used_at: str | None`). Pydantic only lists a
  field in the OpenAPI `required` array when it has no default, so writing
  `= None` silently makes it optional in the generated TypeScript.

The frontend's types are generated from this spec, and CI fails if either
artefact is stale. The prose contract at
`../aidlc-docs/inception/api-contract.md` explains the intent; the spec is
what the code must agree with.

## MCP usage

Create an API key via `POST /api/keys` (or the UI), then Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "japanese-law": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8000/mcp", "--header", "Authorization: Bearer ${JLCA_KEY}"],
      "env": { "JLCA_KEY": "jlca_xxxxxxxx" }
    }
  }
}
```

Clients with native streamable-HTTP support can use `{"type":"http","url":"http://localhost:8000/mcp","headers":{"Authorization":"Bearer jlca_..."}}`.
