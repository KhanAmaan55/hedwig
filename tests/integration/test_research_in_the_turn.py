"""Web research inside a real turn (KNW-02/03, COG-04, docs/13 §13)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from hedwig.brain.planner import CITE_DIRECTIVE, NO_WEB_DIRECTIVE
from hedwig.core.clock import FakeClock
from hedwig.core.config import Config
from hedwig.core.store import Database
from hedwig.core.types import TrustTier
from hedwig.knowledge.web import WebClient
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container, build_container

QUESTION = "What's the latest Python release?"
RESULTS = (Path(__file__).parents[1] / "fixtures" / "web" / "ddg_results.html").read_text()
PAGE = "<html><body><p>Python 3.15.0 is the latest release.</p></body></html>"


def _transport() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "duckduckgo.com/html" in url:
            return httpx.Response(200, text=RESULTS, headers={"content-type": "text/html"})
        if url == "https://www.python.org/downloads/":
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


async def test_with_the_web_off_hedwig_says_it_cannot_look_it_up(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await running_container.brain.run_turn(QUESTION, session_id="s")

    system = llm_provider.calls[-1].messages[0].content
    assert NO_WEB_DIRECTIVE in system
    assert running_container.database.query("SELECT id FROM egress_log") == []


@pytest.fixture
async def online(
    config: Config, clock: FakeClock, database: Database, llm_provider: RecordedProvider
) -> AsyncIterator[Container]:
    web = WebClient(
        database,
        clock,
        http=httpx.AsyncClient(transport=_transport(), follow_redirects=True),
        resolver=_public,
    )
    container = build_container(
        config.model_copy(
            update={"knowledge": config.knowledge.model_copy(update={"web_enabled": True})}
        ),
        clock=clock,
        database=database,
        migrate=False,
        llm_provider=llm_provider,
        web=web,
    )
    await container.start()
    try:
        yield container
    finally:
        await container.stop()


async def test_with_the_web_on_hedwig_researches_cites_and_remembers_it_as_untrusted(
    online: Container, llm_provider: RecordedProvider
) -> None:
    await online.brain.run_turn(QUESTION, session_id="s")
    await online.bus.drain()

    system = llm_provider.calls[-1].messages[0].content
    assert CITE_DIRECTIVE in system
    assert "[1] Download Python | Python.org — https://www.python.org/downloads/" in system
    assert "evil.example" not in system

    notes = [
        m for m in await online.memory.browse() if getattr(m, "title", "").startswith("Researched")
    ]
    assert len(notes) == 1
    assert notes[0].provenance.tier is TrustTier.UNTRUSTED

    # Asked again later, it is recalled — as something read on the web, not something said.
    await online.brain.run_turn("What did you find about Python releases?", session_id="s2")
    later = llm_provider.calls[-1].messages[0].content
    assert "[untrusted" in later and "Researched" in later.split("[untrusted", 1)[1]
