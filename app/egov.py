"""e-Gov Law API v2 client + helpers returning (text_for_llm, summary, {law_id: title})."""
import re

import httpx

BASE = "https://laws.e-gov.go.jp/api/2"
_TAG = re.compile(r"<[^>]+>")


async def _get(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BASE}{path}", params=params)
        try:
            data = r.json()
        except Exception:
            data = {"message": r.text[:200]}
        if r.status_code >= 400:
            raise EgovError(r.status_code, data.get("message") or str(data))
        return data


class EgovError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def norm_article(n: str) -> str:
    """'24' / '３２の２' / '32-2' / '第32条の2' -> '32_2'."""
    n = str(n).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    n = n.replace("第", "").replace("条", "").replace("の", "_").replace("-", "_").replace(" ", "")
    return n


def _walk(node) -> list[str]:
    """Collect sentence texts from a light-json node, one line per item."""
    out: list[str] = []
    if isinstance(node, dict):
        title = node.get("ItemTitle") or next((node[k] for k in node if k.startswith("Subitem") and k.endswith("Title")), None)
        line = []
        for k, v in node.items():
            if k.endswith("Sentence") and isinstance(v, dict):
                s = v.get("Sentence")
                line += [s] if isinstance(s, str) else [x for x in (s or []) if isinstance(x, str)]
        if line:
            out.append(((title + " ") if title else "") + "".join(line))
        for k, v in node.items():
            if k.startswith("Item") or k.startswith("Subitem"):
                if isinstance(v, (list, dict)) and not k.endswith(("Title", "Sentence")):
                    out += _walk(v)
    elif isinstance(node, list):
        for x in node:
            out += _walk(x)
    return out


async def get_article(law_id: str, article_num: str) -> dict:
    n = norm_article(article_num)
    data = await _get(
        f"/law_data/{law_id}",
        {"json_format": "light", "elm": f"MainProvision-Article_{n}"},
    )
    art = (data.get("law_full_text") or {}).get("Article")
    if not art:
        raise EgovError(404, "article not found")
    paras = []
    for p in art.get("Paragraph") or []:
        lines = _walk(p)
        paras.append({"num": str(p.get("Num") or ""), "text": "\n".join(lines)})
    title = (data.get("revision_info") or {}).get("law_title", "")
    return {
        "law_id": law_id,
        "law_title": title,
        "article_num": n,
        "article_title": (art.get("ArticleCaption") or "") + (art.get("ArticleTitle") or ""),
        "paragraphs": paras,
        "source_url": f"https://laws.e-gov.go.jp/law/{law_id}",
    }


def label_art(n: str) -> str:
    return "第" + n.replace("_", "条の") + ("" if "_" in n else "条")


async def tool_get_article(law_id: str, article_num: str):
    try:
        a = await get_article(law_id, article_num)
    except EgovError as e:
        return f"ERROR {e.status}: {e.message}", f"not found ({law_id} Art.{article_num})", {}
    body = "\n".join(f"({p['num']}) {p['text']}" for p in a["paragraphs"])
    text = (
        f"{a['law_title']} (law_id={law_id}) article_num={a['article_num']} {a['article_title']}\n{body}\n"
        f"Citation link: [{a['law_title']} {label_art(a['article_num'])}](cite://{law_id}/{a['article_num']})"
    )
    return text, f"{a['law_title']} {a['article_title']}", {law_id: a["law_title"]}


_POS = re.compile(r"MainProvision(?:-[A-Za-z]+_\d+)*?-Article_([0-9_]+)")


async def tool_search_keyword(keyword: str, law_type: str = "Act", limit: int = 30, sentences_limit: int = 3):
    try:
        data = await _get(
            "/keyword",
            {
                "keyword": keyword,
                "law_type": law_type,
                "limit": limit,
                "sentences_limit": sentences_limit,
                "sentence_text_size": 80,
            },
        )
    except EgovError as e:
        if e.status == 404:
            return "No results. Try a different/wider keyword (wildcards like 契約*).", "0 laws", {}
        return f"ERROR {e.status}: {e.message}", "search failed", {}
    items = data.get("items") or []
    laws: dict[str, str] = {}
    lines = []
    for it in items:
        lid = it["law_info"]["law_id"]
        title = it["revision_info"]["law_title"]
        laws[lid] = title
        lines.append(f"- {title} (law_id={lid})")
        for s in it.get("sentences") or []:
            m = _POS.search(s.get("position", ""))
            art = f" [article_num={m.group(1)}]" if m else ""
            lines.append(f"    ·{art} {_TAG.sub('', s.get('text', ''))}")
    total = data.get("total_count", len(items))
    if not items:
        return "No results. Try a different/wider keyword (wildcards like 契約*).", "0 laws", {}
    return f"{total} total laws matched; showing {len(items)}:\n" + "\n".join(lines), f"{len(items)} laws (of {total})", laws


async def tool_find_law(title: str, limit: int = 5):
    try:
        data = await _get(
            "/laws",
            {"law_title": title, "law_type": "Act", "limit": limit, "repeal_status": "None"},
        )
    except EgovError as e:
        if e.status == 404:
            return "No law found with that title.", "0 laws", {}
        return f"ERROR {e.status}: {e.message}", "lookup failed", {}
    laws: dict[str, str] = {}
    lines = []
    for it in data.get("laws") or []:
        lid = it["law_info"]["law_id"]
        t = (it.get("revision_info") or it.get("current_revision_info") or {}).get("law_title", "")
        laws[lid] = t
        lines.append(f"- {t} (law_id={lid}, {it['law_info'].get('law_num', '')})")
    if not lines:
        return "No law found with that title.", "0 laws", {}
    return "\n".join(lines), f"{len(lines)} laws", laws
