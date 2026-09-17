"""Content-addressed file storage.

The three properties that fall out of addressing by hash — deduplication, verifiable
integrity, and the absence of path traversal — are the ones worth testing, because they are
the reasons for choosing content addressing in the first place (docs/05 §2).
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.errors import NotFoundError
from hedwig.core.ports import HealthStatus
from hedwig.core.storage import ContentAddressedStorage, QuotaExceededError
from hedwig.core.store import Database

# -- writing ---------------------------------------------------------------


async def test_the_digest_is_the_sha256_of_the_content(
    storage: ContentAddressedStorage,
) -> None:
    ref = await storage.put_bytes(b"hello hedwig", media_type="text/plain")

    assert ref.digest == "sha256:" + hashlib.sha256(b"hello hedwig").hexdigest()
    assert ref.size_bytes == 12
    assert ref.media_type == "text/plain"
    assert ref.short == ref.digest.removeprefix("sha256:")[:12]


async def test_identical_content_is_stored_once(
    storage: ContentAddressedStorage,
) -> None:
    first = await storage.put_bytes(b"same bytes")
    second = await storage.put_bytes(b"same bytes")

    assert first.digest == second.digest
    stats = await storage.stats()
    assert stats.blob_count == 1


async def test_round_trip(storage: ContentAddressedStorage) -> None:
    ref = await storage.put_bytes(b"payload")
    assert await storage.get_bytes(ref.digest) == b"payload"


async def test_streaming_write_matches_a_whole_write(
    storage: ContentAddressedStorage,
) -> None:
    data = b"x" * (1 << 20) + b"tail"
    streamed = await storage.put_stream(io.BytesIO(data))

    assert streamed.digest == "sha256:" + hashlib.sha256(data).hexdigest()
    assert streamed.size_bytes == len(data)
    assert await storage.get_bytes(streamed.digest) == data


async def test_put_file_copies_and_leaves_the_original(
    storage: ContentAddressedStorage, tmp_path: Path
) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# notes", encoding="utf-8")

    ref = await storage.put_file(source)

    assert source.exists()
    assert await storage.get_bytes(ref.digest) == b"# notes"
    assert ref.media_type in {"text/markdown", "application/octet-stream"}


async def test_put_file_on_a_missing_path_raises(
    storage: ContentAddressedStorage, tmp_path: Path
) -> None:
    with pytest.raises(NotFoundError):
        await storage.put_file(tmp_path / "absent.txt")


async def test_reading_a_chunked_stream(storage: ContentAddressedStorage) -> None:
    data = b"y" * (2 << 20)
    ref = await storage.put_bytes(data)

    chunks = [chunk async for chunk in await storage.open(ref.digest)]

    assert b"".join(chunks) == data
    assert len(chunks) > 1


# -- limits ----------------------------------------------------------------


async def test_a_blob_over_the_size_limit_is_refused(
    database: Database, clock: FakeClock, tmp_path: Path
) -> None:
    storage = ContentAddressedStorage(tmp_path / "blobs", database, clock=clock, max_blob_bytes=16)
    with pytest.raises(QuotaExceededError):
        await storage.put_bytes(b"x" * 17)


async def test_a_stream_over_the_size_limit_is_refused_mid_write(
    database: Database, clock: FakeClock, tmp_path: Path
) -> None:
    storage = ContentAddressedStorage(tmp_path / "blobs", database, clock=clock, max_blob_bytes=16)
    with pytest.raises(QuotaExceededError):
        await storage.put_stream(io.BytesIO(b"x" * 4096))


async def test_the_quota_is_enforced_across_blobs(
    database: Database, clock: FakeClock, tmp_path: Path
) -> None:
    storage = ContentAddressedStorage(tmp_path / "blobs", database, clock=clock, quota_bytes=20)
    await storage.put_bytes(b"x" * 15)

    with pytest.raises(QuotaExceededError):
        await storage.put_bytes(b"y" * 10)


async def test_quota_pressure_degrades_health(
    database: Database, clock: FakeClock, tmp_path: Path
) -> None:
    storage = ContentAddressedStorage(tmp_path / "blobs", database, clock=clock, quota_bytes=100)
    await storage.put_bytes(b"x" * 95)

    health = await storage.health()
    assert health.status is HealthStatus.DEGRADED


# -- integrity -------------------------------------------------------------


async def test_verify_detects_corruption(storage: ContentAddressedStorage) -> None:
    """The point of content addressing: integrity is checkable at any time."""
    ref = await storage.put_bytes(b"trustworthy")
    assert await storage.verify(ref.digest) is True

    path = storage._path_for(ref.digest)
    path.write_bytes(b"tampered")

    assert await storage.verify(ref.digest) is False


async def test_verify_on_a_missing_blob_is_false(storage: ContentAddressedStorage) -> None:
    assert await storage.verify("sha256:" + "0" * 64) is False


async def test_paths_are_derived_from_the_hash_not_from_input(
    storage: ContentAddressedStorage, tmp_path: Path
) -> None:
    """Path traversal is not a category of bug that exists here."""
    ref = await storage.put_bytes(b"content")
    path = storage._path_for(ref.digest)

    hex_digest = ref.digest.removeprefix("sha256:")
    assert path.name == hex_digest
    assert path.parent.name == hex_digest[2:4]
    assert (tmp_path / "blobs") in path.parents


# -- reading absent blobs --------------------------------------------------


async def test_reading_an_absent_blob_raises(storage: ContentAddressedStorage) -> None:
    with pytest.raises(NotFoundError):
        await storage.get_bytes("sha256:" + "0" * 64)


async def test_stat_and_exists(storage: ContentAddressedStorage) -> None:
    ref = await storage.put_bytes(b"here")

    assert await storage.exists(ref.digest) is True
    assert await storage.stat(ref.digest) is not None
    assert await storage.exists("sha256:" + "0" * 64) is False
    assert await storage.stat("sha256:" + "0" * 64) is None


# -- deletion --------------------------------------------------------------


async def test_delete_removes_bytes_and_metadata(
    storage: ContentAddressedStorage,
) -> None:
    ref = await storage.put_bytes(b"transient")

    assert await storage.delete(ref.digest) is True
    assert await storage.exists(ref.digest) is False
    assert (await storage.stats()).blob_count == 0


async def test_deleting_an_absent_blob_reports_false(
    storage: ContentAddressedStorage,
) -> None:
    assert await storage.delete("sha256:" + "0" * 64) is False


async def test_prune_reconciles_disk_and_metadata(
    storage: ContentAddressedStorage, database: Database
) -> None:
    """Only a crash between the two writes can desynchronise them; it should be visible."""
    ref = await storage.put_bytes(b"orphan me")
    database.execute("DELETE FROM blob WHERE digest = ?", (ref.digest,))

    removed = await storage.prune_orphans()

    assert any(entry.startswith("file:") for entry in removed)
    assert not storage._path_for(ref.digest).exists()


async def test_prune_removes_rows_without_files(
    storage: ContentAddressedStorage,
) -> None:
    ref = await storage.put_bytes(b"vanishing")
    storage._path_for(ref.digest).unlink()

    removed = await storage.prune_orphans()

    assert any(entry.startswith("row:") for entry in removed)
    assert (await storage.stats()).blob_count == 0


# -- stats -----------------------------------------------------------------


async def test_stats_sum_sizes(storage: ContentAddressedStorage) -> None:
    await storage.put_bytes(b"a" * 10)
    await storage.put_bytes(b"b" * 20)

    stats = await storage.stats()
    assert stats.blob_count == 2
    assert stats.total_bytes == 30
