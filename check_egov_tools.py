"""Smoke test of the e-Gov tools: uv run python check_egov_tools.py"""
import asyncio

from app import egov


async def main():
    for coro in (
        egov.tool_find_law("消費者契約法"),
        egov.tool_search_keyword('"損害賠償" AND "免責"'),
        egov.tool_get_article("322AC0000000049", "24"),
        egov.tool_get_article("129AC0000000089", "3_2"),
        egov.tool_get_article("129AC0000000089", "9999"),
    ):
        text, summary, laws = await coro
        print("==", summary, laws)
        print(text[:400])


asyncio.run(main())
