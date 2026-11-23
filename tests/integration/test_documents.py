"""The user's own documents (KNW-01, docs/13 §14): read narrowly, recalled with attribution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.errors import InvalidRequestError
from hedwig.core.token import TokenFile
from hedwig.core.types import MemoryKind, TrustTier
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container

TIDES = """# Tides

Tides are the rise and fall of the sea.

## Neap tides

Neap tides happen when the sun and the moon pull at right angles, so the range is smallest.

## Spring tides

Spring tides have nothing to do with spring; the sun and moon line up.
"""


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "tides.md").write_text(TIDES)
    (folder / "garden.txt").write_text("Plant garlic in October. Water the tomatoes daily.")
    (folder / ".env").write_text("TOKEN=abc")  # hidden: never even read
    (folder / "credentials.txt").write_text("bank login notes")
    (folder / "keys.md").write_text("server\n-----BEGIN RSA PRIVATE KEY-----\nMIIE\n")
    (folder / "photo.png").write_bytes(b"\x89PNG\x00\x00")
    (folder / "huge.txt").write_text("x" * 3_000_000)
    (folder / "data.txt").write_bytes(b"\x00\x01binary")
    hidden = folder / ".private"
    hidden.mkdir()
    (hidden / "diary.md").write_text("My secret diary.")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("Outside the folder.")
    (folder / "link.md").symlink_to(outside)
    return folder


async def test_a_folder_is_read_narrowly_and_secrets_are_reported(
    running_container: Container, notes: Path
) -> None:
    report = await running_container.library.add(str(notes))

    assert report["added"] == 2
    reasons = {Path(s["path"]).name: s["reason"] for s in report["skipped"]}
    assert reasons == {
        "credentials.txt": "looks like a secrets file",
        "keys.md": "contains what looks like a key",
        "huge.txt": "larger than the size limit",
        "data.txt": "not text",
    }
    paths = {Path(d["path"]).name for d in running_container.library.listing()["documents"]}
    assert paths == {"tides.md", "garden.txt"}, "hidden, symlinked-out and non-text files unread"
    rows = running_container.database.query("SELECT text FROM document_chunk")
    assert not any("PRIVATE KEY" in r["text"] or "secret diary" in r["text"] for r in rows)


@pytest.mark.parametrize("root", ["/", "~"])
async def test_the_whole_disk_or_home_is_refused(running_container: Container, root: str) -> None:
    with pytest.raises(InvalidRequestError):
        await running_container.library.add(root)


async def test_hedwigs_own_data_is_refused(running_container: Container) -> None:
    with pytest.raises(InvalidRequestError):
        await running_container.library.add(str(running_container.config.runtime.data_dir))


async def test_a_passage_is_recalled_as_the_users_document_with_its_heading(
    running_container: Container, notes: Path, llm_provider: RecordedProvider
) -> None:
    await running_container.library.add(str(notes))

    working = await running_container.retrieval.search(["neap tides"])

    top = max(working.items, key=lambda item: item.score)
    assert top.kind is MemoryKind.DOCUMENT and top.provenance.tier is TrustTier.CURATED
    assert top.text.startswith("tides > Tides > Neap tides:")
    assert top.provenance.source_ref == str(notes / "tides.md")

    await running_container.brain.run_turn("What are neap tides again?", session_id="s")
    system = llm_provider.calls[-1].messages[0].content
    block = system.split("[your documents", 1)[1].split("\n\n", 1)[0]
    assert "tides.md > tides > Tides > Neap tides:" in block
    memory_block = system.split("[memory", 1)[1] if "[memory" in system else ""
    assert "right angles" not in memory_block.split("[your documents")[0]
    accessed = running_container.database.query(
        "SELECT memory_kind FROM memory_access WHERE memory_kind = 'document'"
    )
    assert accessed == [], "a document is the user's file, not a memory to reinforce"


async def test_changes_on_disk_are_followed(running_container: Container, notes: Path) -> None:
    library = running_container.library
    await library.add(str(notes))

    (notes / "garden.txt").write_text("Plant garlic in November now.")
    (notes / "tides.md").unlink()
    (notes / "bread.md").write_text("# Bread\n\nFeed the starter twice a day.")
    report = await library.reindex()

    assert (report["added"], report["updated"], report["removed"]) == (1, 1, 1)
    working = await running_container.retrieval.search(["garlic November"])
    assert any("November" in item.text for item in working.items)
    assert not any(
        "Neap" in item.text for item in (await running_container.retrieval.search(["neap"])).items
    )


async def test_a_file_set_aside_stays_aside_until_the_folder_is_added_again(
    running_container: Container, notes: Path
) -> None:
    library = running_container.library
    await library.add(str(notes))
    tides = next(d for d in library.listing()["documents"] if d["path"].endswith("tides.md"))

    await library.remove(tides["id"])
    await library.reindex()
    assert not (await running_container.retrieval.search(["neap tides"])).items

    await library.add(str(notes))
    assert (await running_container.retrieval.search(["neap tides"])).items


async def test_erasing_a_phrase_sets_its_file_aside(
    running_container: Container, notes: Path
) -> None:
    await running_container.library.add(str(notes))
    preview = await running_container.purger.preview("phrase", "right angles")
    assert preview["counts"] == {"passages of your documents": 3}, "the whole file, not half"

    await running_container.purger.purge("phrase", "right angles", preview["confirm"])

    await running_container.library.reindex()
    assert not (await running_container.retrieval.search(["neap tides"])).items
    assert (notes / "tides.md").exists(), "the user's own file is never touched"


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_documents_api(api: AsyncClient, notes: Path) -> None:
    added = await api.post("/v1/documents", json={"path": str(notes)})
    assert added.status_code == 200 and added.json()["added"] == 2
    listing = (await api.get("/v1/documents")).json()
    assert [r["path"] for r in listing["roots"]] == [str(notes.resolve())]
    assert (await api.post("/v1/documents", json={"path": "/"})).status_code == 400
    first = listing["documents"][0]["id"]
    assert (await api.delete(f"/v1/documents/{first}")).status_code == 200
    gone = await api.delete("/v1/documents", params={"path": str(notes)})
    assert gone.json() == {"removed": 2}
    assert (await api.get("/v1/documents", headers={"Authorization": ""})).status_code == 401
