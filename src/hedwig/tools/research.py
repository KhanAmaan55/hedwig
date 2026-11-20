"""The `research` tool: search, read the top pages, cite (docs/13 §13).

One tool rather than `web_search` + `fetch_url`, so the rule-based planner needs one decision,
not a loop. Output is untrusted data with numbered sources; a page the injection detector flags
is withheld and counted, never shown.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from hedwig.core.ports.brain import TrustTier
from hedwig.core.ports.memory import EpisodeKind
from hedwig.core.types import SalienceInputs
from hedwig.core.types import TrustTier as MemoryTrust
from hedwig.knowledge.injection import scan
from hedwig.knowledge.web import Page, SearchResult, WebClient, WebRefusedError
from hedwig.memory.capture import CaptureService, EpisodeCandidate
from hedwig.tools.registry import Tool, ToolError

Remember = Callable[[str, Sequence[SearchResult]], Awaitable[None]]

READ_TOP = 2
EXCERPT = 900


def research_tool(web: WebClient, *, remember: Remember | None = None) -> Tool:
    async def run(arguments: Mapping[str, Any]) -> str:
        query = str(arguments["query"]).strip()
        try:
            results = await web.search(query)
        except WebRefusedError as error:
            raise ToolError(f"Searching failed: {error}.") from error
        if not results:
            raise ToolError("The search found nothing.")

        sources = [r for r in results if not scan(f"{r.title} {r.snippet}").quarantined]
        withheld = len(results) - len(sources)
        pages = await asyncio.gather(*(_read(web, r.url) for r in sources[:READ_TOP]))
        readable: dict[str, Page] = {}
        for page in pages:
            if page is None:
                continue
            if page.scan.quarantined:
                withheld += 1
            else:
                readable[page.url] = page

        lines = [f'Researched on the web just now for: "{query}"']
        shown = [
            r
            for r in sources
            if not (r.url in {p.url for p in pages if p} and r.url not in readable)
        ]
        for number, result in enumerate(shown, start=1):
            page = readable.get(result.url)
            excerpt = (page.text if page else result.snippet)[:EXCERPT]
            lines.append(f"[{number}] {result.title} — {result.url}\n    {excerpt}")
        if withheld:
            lines.append(f"({withheld} source(s) withheld: they contained text aimed at an AI.)")
        if remember is not None and shown:
            await remember(query, shown)
        return "\n".join(lines)

    return Tool(
        name="research",
        description="Search the web and read the top results, for current information.",
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 300}},
            "required": ["query"],
            "additionalProperties": False,
        },
        run=run,
        side_effects="network",
        trust=TrustTier.UNTRUSTED,
        timeout_seconds=35.0,
    )


async def _read(web: WebClient, url: str) -> Page | None:
    try:
        return await web.read(url)
    except WebRefusedError:
        return None


def research_memory(capture: CaptureService) -> Remember:
    """Keep a note of what was researched, as untrusted knowledge (docs/13 §13, KNW-03).

    Not the pages — the question, the sources and their snippets — so recall can later say
    "I looked this up" and point at where, without the web text ever becoming the user's word.
    """

    async def remember(query: str, sources: Sequence[SearchResult]) -> None:
        listing = "; ".join(f"[{i}] {s.title} ({s.url})" for i, s in enumerate(sources[:5], 1))
        findings = " ".join(s.snippet for s in sources[:2])[:600]
        await capture.capture(
            episodes=(
                EpisodeCandidate(
                    title=f"Researched: {query}"[:72],
                    content=f'Researched "{query}" on the web. Sources: {listing}. Found: {findings}',
                    kind=EpisodeKind.OBSERVATION,
                    trust=MemoryTrust.UNTRUSTED,
                    source_ref=sources[0].url,
                    salience_inputs=SalienceInputs(novelty=0.5),
                ),
            )
        )

    return remember
