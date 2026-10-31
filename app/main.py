from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .mcp_server import build_mcp_app
from .routes import router

_inner, _mcp_asgi = build_mcp_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with _inner.router.lifespan_context(_inner):
        yield


_fastapi = FastAPI(title="Japanese Law Contract Analyzer", lifespan=lifespan)
app = _fastapi


class _McpSlash:
    """Accept POST /mcp without trailing slash (avoid 307)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, k):
        return getattr(self.inner, k)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.inner(scope, receive, send)
_fastapi.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{p}" for p in range(5173, 5181)] + [f"http://127.0.0.1:{p}" for p in range(5173, 5181)],
    allow_methods=["*"],
    allow_headers=["*"],
)
_fastapi.include_router(router)
_fastapi.mount("/mcp", _mcp_asgi)

app = _McpSlash(_fastapi)
