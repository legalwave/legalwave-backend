from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse

from . import egov
from .agent import run_agent
from .auth import verify_api_key
from .db import SessionLocal

mcp = MCPServer("japanese-law-contract-analyzer")


@mcp.tool()
async def analyze_contract_risk(query: str, document_text: str = "") -> str:
    """Analyze a contract (or legal question) against Japanese law. Returns markdown with per-clause RED/YELLOW/GREEN verdicts and cite:// citations to e-Gov statutes."""
    prompt = query
    if document_text:
        prompt += f"\n\n--- Contract text ---\n{document_text[:80000]}"
    out = ""
    async for e in run_agent([], prompt):
        if e["type"] == "text_delta":
            out += e["delta"]
        elif e["type"] == "error":
            return f"ERROR: {e['message']}"
    return out


@mcp.tool()
async def search_japanese_law(keyword: str) -> str:
    """Keyword search of Japanese statutes via e-Gov. Supports wildcards (契約*) and boolean operators. Returns a short markdown list of hits."""
    text, _, _ = await egov.tool_search_keyword(keyword)
    return text


class BearerKeyAuth:
    """ASGI middleware: require Authorization: Bearer jlca_... API key."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = dict(scope["headers"]).get(b"authorization", b"").decode()
            ok = False
            if h.lower().startswith("bearer "):
                async with SessionLocal() as db:
                    ok = await verify_api_key(db, h[7:].strip()) is not None
            if not ok:
                await JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_mcp_app():
    inner = mcp.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True)
    return inner, BearerKeyAuth(inner)
