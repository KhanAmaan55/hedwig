"""The API token file (ADR-0022)."""

from __future__ import annotations

import stat
from pathlib import Path

from hedwig.core.token import TokenFile


def test_ensure_creates_a_private_token_once(tmp_path: Path) -> None:
    token_file = TokenFile(tmp_path / "token")

    first = token_file.ensure()
    second = token_file.ensure()

    assert first == second, "a second start must not replace the token"
    assert len(first) >= 43  # 32 bytes, URL-safe base64
    assert stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600
    assert token_file.is_private()


def test_a_fresh_reader_sees_the_same_token(tmp_path: Path) -> None:
    """The backend and the CLI are different processes, each with its own `TokenFile`."""
    token = TokenFile(tmp_path / "token").ensure()
    assert TokenFile(tmp_path / "token").read() == token


def test_rotation_is_honoured_without_a_restart(tmp_path: Path) -> None:
    server = TokenFile(tmp_path / "token")
    old = server.ensure()
    assert server.matches(old)

    new = TokenFile(tmp_path / "token").rotate()  # what `hedwig token rotate` does

    assert new != old
    assert not server.matches(old), "the server kept accepting a rotated token"
    assert server.matches(new)
    assert stat.S_IMODE((tmp_path / "token").stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".token.*")), "rotation left its staging file behind"


def test_a_missing_or_empty_file_matches_nothing(tmp_path: Path) -> None:
    token_file = TokenFile(tmp_path / "token")
    assert token_file.read() is None
    assert not token_file.matches("")
    assert not token_file.matches("anything")

    (tmp_path / "token").write_text("\n")
    assert token_file.read() is None
    assert not token_file.matches("")


def test_deleting_the_file_fails_closed(tmp_path: Path) -> None:
    token_file = TokenFile(tmp_path / "token")
    token = token_file.ensure()
    (tmp_path / "token").unlink()
    assert not token_file.matches(token)


def test_wrong_tokens_do_not_match(tmp_path: Path) -> None:
    token_file = TokenFile(tmp_path / "token")
    token = token_file.ensure()
    assert not token_file.matches(token[:-1])
    assert not token_file.matches(token + "x")
    assert not token_file.matches(token.upper() if token.upper() != token else token + "!")
