"""Export and purge (SEC-02, docs/21 §7.4, ADR-0024), on a real container and a real file.

The completeness test is the one docs/21 §10 asks for: content goes in through the real flows
(turns, events, capture, reflection, forgetting, a nightly summary, a backup), is purged, and
then the database *file* is read and must not contain it. A query can say a row is gone while
its bytes sit in a freed page, an FTS segment or the WAL.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.config import Config
from hedwig.core.errors import ConflictError
from hedwig.core.ids import new_id
from hedwig.core.ports.memory import Episode, EpisodeKind, ForgetReason
from hedwig.core.token import TokenFile
from hedwig.core.types import MemoryId
from hedwig.llm.recorded import RecordedProvider, json_response
from hedwig.privacy import Purger
from hedwig.reflection import SessionReflector
from hedwig.wiring import Container, build_container

SECRET = "Zelphine"
PLACE = "Quorvalla"
SAID = f"My sister {SECRET} lives in {PLACE} and adores marmalade sandwiches."
KEPT = "I have been drinking a lot of jasmine tea this winter."


@pytest.fixture
async def on_disk(
    config: Config, clock: FakeClock, llm_provider: RecordedProvider
) -> AsyncIterator[Container]:
    """A container on a real database file: the byte scan needs one."""
    container = build_container(config, clock=clock, llm_provider=llm_provider)
    await container.start()
    try:
        yield container
    finally:
        await container.stop()


def _file_bytes(container: Container) -> bytes:
    path = container.database.path
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


async def _talk(container: Container, session: str, line: str) -> None:
    await container.brain.run_turn(line, session_id=session)
    await container.bus.drain()


async def _seed(container: Container, llm_provider: RecordedProvider) -> None:
    """The secret, everywhere HEDWIG puts things."""
    session = await container.brain.open_session(channel="web")
    await _talk(container, session, SAID)
    await _talk(container, session, "She is visiting next month, which I am excited about.")
    llm_provider.script(
        "reflect.session",
        json_response(
            {
                "worth_remembering": True,
                "title": f"About {SECRET}",
                "summary": f"The user's sister {SECRET} lives in {PLACE}.",
                "journal": [],
                "facts": [
                    {
                        "statement": f"The user's sister {SECRET} lives in {PLACE}.",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
    )
    reflector = container.registry.get("reflection")
    assert isinstance(reflector, SessionReflector)
    await reflector.reflect(session)

    # A forgotten copy: its body is archived in `tombstone`.
    archived = MemoryId(new_id("ep"))
    await container.memory.add_episode(
        Episode(
            id=archived,
            occurred_at=container.clock.now(),
            title="A note",
            content=f"Remember that {SECRET} prefers the window seat on trains to {PLACE}.",
        )
    )
    await container.memory.tombstone(archived, ForgetReason.USER_REQUEST)

    # Something unrelated, which must survive.
    other = await container.brain.open_session(channel="web")
    await _talk(container, other, KEPT)

    # A backup taken before the purge holds everything.
    container.config.runtime.backups_dir.mkdir(parents=True, exist_ok=True)
    container.database.backup_to(container.config.runtime.backups_dir / "pre-0099.db")


# =========================================================================
# Purge
# =========================================================================


async def test_a_purged_phrase_leaves_no_trace_in_the_database_file(
    on_disk: Container, llm_provider: RecordedProvider
) -> None:
    await _seed(on_disk, llm_provider)
    assert SECRET.encode() in _file_bytes(on_disk), "the seed did not reach the file"

    preview = await on_disk.purger.preview("phrase", SECRET)
    assert preview["counts"]["messages"] == 1
    assert preview["counts"]["beliefs"] >= 1 and preview["counts"]["episodes"] >= 2
    assert preview["backups"] == 1
    assert any(SECRET in example for example in preview["examples"])
    assert SECRET.encode() in _file_bytes(on_disk), "a preview must change nothing"

    receipt = await on_disk.purger.purge("phrase", SECRET, preview["confirm"])

    assert receipt["verified"] == "file"
    raw = _file_bytes(on_disk).lower()
    assert SECRET.lower().encode() not in raw
    assert PLACE.lower().encode() not in raw, "the place survived in some copy of the sentence"
    assert b"marmalade" not in raw
    assert KEPT.encode() in _file_bytes(on_disk), "an unrelated conversation was erased"
    assert not list(on_disk.config.runtime.backups_dir.glob("*")), "a backup still holds it"
    assert receipt["backups_deleted"] == 1
    assert on_disk.database.foreign_key_check() == []
    assert on_disk.database.integrity_check() == []


async def test_the_receipt_records_that_it_happened_and_nothing_of_what(
    on_disk: Container, llm_provider: RecordedProvider
) -> None:
    await _seed(on_disk, llm_provider)
    preview = await on_disk.purger.preview("phrase", SECRET)
    await on_disk.purger.purge("phrase", SECRET, preview["confirm"])

    (receipt,) = on_disk.purger.receipts()
    assert receipt["scope_kind"] == "phrase" and receipt["counts"]["messages"] == 1
    assert SECRET not in json.dumps(receipt)


async def test_erasing_a_memory_takes_what_was_made_from_it(
    running_container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    yesterday = clock.now() - timedelta(days=1)
    leaves = []
    for hour in range(5):
        episode = MemoryId(new_id("ep"))
        text = f"User: about {SECRET}" if hour == 0 else f"User: compiler work, part {hour}"
        await running_container.memory.add_episode(
            Episode(
                id=episode,
                occurred_at=yesterday.replace(hour=9 + hour),
                title=f"chat {hour}",
                content=text,
                kind=EpisodeKind.INTERACTION,
            )
        )
        leaves.append(episode)
    llm_provider.script(
        "reflect.day",
        json_response({"title": "A day", "summary": f"The user talked about {SECRET}."}),
    )
    await running_container.nightly.run(force=True)
    day = yesterday.astimezone().date().isoformat()
    summary = await running_container.memory.summary_for(f"day:{day}")
    assert summary is not None

    preview = await running_container.purger.preview("memory", leaves[0])
    assert preview["counts"] == {"episodes": 2}, "the day summary paraphrases it"
    receipt = await running_container.purger.purge("memory", leaves[0], preview["confirm"])

    assert receipt["verified"] == "query"  # in-memory: no file to read
    assert await running_container.memory.inspect(summary.id) is None
    for sibling in leaves[1:]:
        assert await running_container.memory.inspect(sibling) is not None


async def test_erasing_a_conversation(running_container: Container) -> None:
    gone = await running_container.brain.open_session(channel="web")
    await _talk(running_container, gone, SAID)
    kept = await running_container.brain.open_session(channel="web")
    await _talk(running_container, kept, KEPT)

    preview = await running_container.purger.preview("session", gone)
    assert preview["counts"]["conversations"] == 1 and preview["counts"]["messages"] == 2
    await running_container.purger.purge("session", gone, preview["confirm"])

    assert await running_container.history.get_session(gone) is None
    assert await running_container.history.get_session(kept) is not None
    db = running_container.database
    for table in ("turn", "event", "working_set_log", "relationship_tally", "message"):
        rows = db.query(f"SELECT * FROM {table}")
        assert not any(gone in json.dumps(dict(r), default=str) for r in rows), table
    assert db.foreign_key_check() == []


async def test_a_purge_refuses_when_what_it_would_erase_has_changed(
    running_container: Container,
) -> None:
    session = await running_container.brain.open_session(channel="web")
    await _talk(running_container, session, SAID)
    preview = await running_container.purger.preview("phrase", SECRET)
    await _talk(running_container, session, f"Also, {SECRET} has a cat.")

    with pytest.raises(ConflictError):
        await running_container.purger.purge("phrase", SECRET, preview["confirm"])
    assert (await running_container.purger.preview("phrase", SECRET))["counts"]["messages"] == 2


async def test_a_purge_waits_for_a_reply_in_progress(running_container: Container) -> None:
    async def nothing() -> None:
        return None

    busy = Purger(
        database=running_container.database,
        clock=running_container.clock,
        backups_dir=running_container.config.runtime.backups_dir,
        is_busy=lambda: True,
        drain=nothing,
    )
    with pytest.raises(ConflictError):
        await busy.purge("phrase", SECRET, "whatever")


# =========================================================================
# Export, and the API
# =========================================================================


@pytest.fixture
async def api(on_disk: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(on_disk.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(on_disk)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_export_is_complete_readable_and_holds_no_token(
    on_disk: Container, api: AsyncClient
) -> None:
    session = await on_disk.brain.open_session(channel="web")
    await _talk(on_disk, session, SAID)

    response = await api.post("/v1/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = {name.split("/", 1)[1] for name in archive.namelist()}
    assert {"README.txt", "manifest.json", "hedwig.db", "memories.md", "goals.md"} <= names
    conversation = next(n for n in archive.namelist() if "/conversations/" in n)
    assert SAID in archive.read(conversation).decode()
    manifest = json.loads(
        archive.read(next(n for n in archive.namelist() if n.endswith("manifest.json")))
    )
    assert manifest["format"] == 1 and manifest["rows"]["message"] == 2
    root = archive.namelist()[0].split("/", 1)[0]
    for name, described in manifest["files"].items():
        assert len(archive.read(f"{root}/{name}")) == described["bytes"], name
    token = TokenFile(on_disk.config.runtime.token_path).read()
    assert token and not any(token.encode() in archive.read(n) for n in archive.namelist())
    assert not list(Path(on_disk.config.runtime.data_dir).glob("**/*.zip")), "a copy was left"


async def test_the_privacy_api(on_disk: Container, api: AsyncClient) -> None:
    session = await on_disk.brain.open_session(channel="web")
    await _talk(on_disk, session, SAID)

    preview = (
        await api.post("/v1/purge/preview", json={"scope": "phrase", "value": SECRET})
    ).json()
    wrong = await api.post(
        "/v1/purge", json={"scope": "phrase", "value": SECRET, "confirm": "000000000000"}
    )
    assert wrong.status_code == 409
    done = await api.post(
        "/v1/purge", json={"scope": "phrase", "value": SECRET, "confirm": preview["confirm"]}
    )
    assert done.status_code == 200 and done.json()["verified"] == "file"
    receipts = (await api.get("/v1/purge/receipts")).json()["receipts"]
    assert len(receipts) == 1
    short = await api.post("/v1/purge/preview", json={"scope": "phrase", "value": "ab"})
    assert short.status_code == 400
    anonymous = await api.post("/v1/export", headers={"Authorization": ""})
    assert anonymous.status_code == 401
