import json
import re
from typing import AsyncIterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from . import egov
from .config import settings

SYSTEM_PROMPT = """You are a Japanese-law contract risk analyst. You review contracts (and answer legal questions) against Japanese statutes retrieved live from the e-Gov Law API using your tools.

Workflow:
1. If a contract text is provided, split it into clauses (numbered Clause 1, 2, ...).
2. For relevant clauses, call tools to find and read the applicable statutes (e.g. 民法, 消費者契約法, 労働基準法, 下請代金支払遅延等防止法(下請法), 借地借家法, 個人情報保護法 as relevant). Prefer find_law_by_title to get a law_id, then get_law_article for exact text; use search_laws_by_keyword for discovery. Do NOT write any prose before or between tool calls; call tools first, then write the final answer once.
3. Never cite a law_id/article you did not retrieve through a tool in this conversation. Never invent article numbers.
4. Answer in the user's language (Japanese user -> Japanese, English -> English). Statute names stay in Japanese.

Final answer format (markdown, exactly):
- Near the top: `**Overall: RED**` (or YELLOW / GREEN) - the worst clause verdict, plus a one or two sentence summary.
- One block per clause that matters:
### RED · Clause 7 — Unilateral amendment
> quoted clause text
Explanation of the risk and suggested fix. Citations as markdown links: [民法 第1条](cite://<law_id>/<article_num>) where law_id and article_num are exactly those returned by tools (article_num like 24 or 32_2 for 32条の2).
Verdict token is exactly one of RED (noncompliant/void/illegal), YELLOW (risky/unfavourable), GREEN (compliant).
- Final line, exactly: `_Not legal advice · Verdicts are informational_`

If no contract is provided (general legal question), still answer concisely with cite:// citations, an `**Overall: ...**` line only if you assess a document, and the final disclaimer line.
"""


def _model():
    return ChatGoogleGenerativeAI(model=settings.gemini_model, google_api_key=settings.google_api_key)


@tool(response_format="content_and_artifact")
async def search_laws_by_keyword(keyword: str, law_type: str = "Act", limit: int = 30):
    """Full-text keyword search across Japanese laws (e-Gov /keyword). Supports wildcards (契約*, 労働?基準) and boolean ops ("損害賠償" AND "免責"). law_type: Act (default), Rule, CabinetOrder, ... Returns matching laws with law_id and snippets that include article_num."""
    text, summary, laws = await egov.tool_search_keyword(keyword, law_type, min(max(limit, 1), 60))
    return text, {"summary": summary, "laws": laws}


@tool(response_format="content_and_artifact")
async def get_law_article(law_id: str, article_num: str):
    """Fetch the exact text of one article. law_id like 129AC0000000089 (民法); article_num like "24" or "32_2" (for 32条の2). Returns paragraphs and a ready-to-use cite:// link."""
    text, summary, laws = await egov.tool_get_article(law_id, article_num)
    return text, {"summary": summary, "laws": laws}


@tool(response_format="content_and_artifact")
async def find_law_by_title(title: str):
    """Look up a law's law_id by (partial) title, e.g. 民法, 消費者契約法, 労働基準法, 下請代金支払遅延等防止法, 借地借家法."""
    text, summary, laws = await egov.tool_find_law(title)
    return text, {"summary": summary, "laws": laws}


TOOLS = [search_laws_by_keyword, get_law_article, find_law_by_title]

_CITE = re.compile(r"\[([^\]]+)\]\(cite://([^/)\s]+)/([^)\s]+)\)")


def extract_citations(text: str, law_titles: dict[str, str]) -> list[dict]:
    items, seen = [], set()
    for label, law_id, art in _CITE.findall(text):
        if (law_id, art) in seen:
            continue
        seen.add((law_id, art))
        items.append(
            {
                "id": f"c{len(items) + 1}",
                "label": label,
                "law_id": law_id,
                "law_title": law_titles.get(law_id) or label.split(" ")[0],
                "article": art,
            }
        )
    return items


def build_history(msgs) -> list:
    out = []
    for m in msgs:
        if m.role == "user":
            c = m.content
            if m.attachment_text:
                c += f"\n\n--- Attached file: {m.attachment_filename} ---\n{m.attachment_text}"
            out.append(HumanMessage(c))
        else:
            out.append(AIMessage(m.content))
    return out


async def run_agent(history: list, prompt: str) -> AsyncIterator[dict]:
    """Yield contract events (stage/tool_call/tool_result/text_delta/citations) or a terminal error. Caller adds chat/done."""
    yield {"type": "stage", "stage": "fetching_statutes", "status": "active", "label": "Fetching statutes"}
    if not settings.google_api_key:
        yield {"type": "error", "message": "GOOGLE_API_KEY is not configured on the server. Set it in backend/.env and restart."}
        return
    try:
        agent = create_agent(_model(), TOOLS, system_prompt=SYSTEM_PROMPT)
        titles: dict[str, str] = {}
        full = ""
        analyzing = False
        async for mode, payload in agent.astream(
            {"messages": history + [HumanMessage(prompt)]},
            stream_mode=["messages", "updates"],
            config={"recursion_limit": 40},
        ):
            if mode == "messages":
                chunk, meta = payload
                if meta.get("langgraph_node") != "model" or not hasattr(chunk, "tool_call_chunks"):
                    continue
                delta = chunk.text if isinstance(chunk.text, str) else ""
                if delta:
                    if not analyzing:
                        analyzing = True
                        yield {"type": "stage", "stage": "fetching_statutes", "status": "complete", "label": "Statutes fetched"}
                        yield {"type": "stage", "stage": "analyzing", "status": "active", "label": "Analyzing clauses"}
                    full += delta
                    yield {"type": "text_delta", "delta": delta}
            else:
                for node, upd in payload.items():
                    for m in (upd or {}).get("messages", []) if isinstance(upd, dict) else []:
                        if isinstance(m, AIMessage):
                            for tc in m.tool_calls:
                                yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "args": tc["args"]}
                        elif isinstance(m, ToolMessage):
                            art = m.artifact if isinstance(m.artifact, dict) else {}
                            titles.update(art.get("laws", {}))
                            yield {
                                "type": "tool_result",
                                "id": m.tool_call_id,
                                "name": m.name,
                                "summary": art.get("summary") or str(m.content)[:120],
                            }
        if not analyzing:
            yield {"type": "stage", "stage": "fetching_statutes", "status": "complete", "label": "Statutes fetched"}
            yield {"type": "stage", "stage": "analyzing", "status": "active", "label": "Analyzing clauses"}
        yield {"type": "stage", "stage": "analyzing", "status": "complete", "label": "Analysis complete"}
        yield {"type": "stage", "stage": "grounding", "status": "active", "label": "Grounding citations"}
        yield {"type": "citations", "items": extract_citations(full, titles)}
        yield {"type": "stage", "stage": "grounding", "status": "complete", "label": "Citations grounded"}
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "message": f"Agent error: {type(e).__name__}: {str(e)[:500]}"}


def ev(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False) + "\n"
