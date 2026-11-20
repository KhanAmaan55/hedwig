"""`hedwig chat` and `hedwig token` (UX-01, FND-01).

The chat loop runs against the real application through `HedwigClient` — the same client
the terminal uses — so these tests are also the CLI-parity check: whatever the CLI can do,
it did through the public API (docs/16 §10).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hedwig.api.app import create_app
from hedwig.cli import Kind, app, run_chat
from hedwig.client import HedwigClient
from hedwig.core.token import TokenFile
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container


class Terminal:
    """Scripted input, captured output."""

    def __init__(self, *lines: str) -> None:
        self._lines = list(lines)
        self.output: list[tuple[Kind, str]] = []

    def read(self, _prompt: str) -> str:
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)

    def write(self, text: str, kind: Kind) -> None:
        self.output.append((kind, text))

    def said(self, kind: Kind) -> list[str]:
        return [text for k, text in self.output if k == kind]


@pytest.fixture
def http(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as client:  # runs the lifespan: a started HEDWIG
        yield client


@pytest.fixture
def client(http: TestClient, container: Container) -> HedwigClient:
    token = TokenFile(container.config.runtime.token_path).read()
    assert token
    return HedwigClient("http://testserver", token, http=http)


def test_a_conversation_from_the_terminal(
    client: HedwigClient, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("compose.reply", "Hello! Lovely to meet you.")
    terminal = Terminal("Hello HEDWIG, nice to meet you.", "", "/quit")

    code = run_chat(client, session_id=None, read=terminal.read, write=terminal.write)

    assert code == 0
    assert terminal.said("hedwig") == ["Hello! Lovely to meet you."]
    assert "hedwig chat --session sess_" in terminal.said("info")[-1]


def test_resuming_shows_where_we_left_off(client: HedwigClient) -> None:
    session_id = client.open_session()
    client.send(session_id, "We were talking about the memory architecture.")
    terminal = Terminal("/quit")

    code = run_chat(client, session_id=session_id, read=terminal.read, write=terminal.write)

    assert code == 0
    assert "you: We were talking about the memory architecture." in terminal.said("info")
    assert any(line.startswith("HEDWIG: ") for line in terminal.said("info"))


def test_an_unknown_session_ends_the_chat(client: HedwigClient) -> None:
    terminal = Terminal()
    assert run_chat(client, session_id="sess_nope", read=terminal.read, write=terminal.write) == 1
    assert "sess_nope" in terminal.said("error")[0]


def test_end_of_input_leaves_cleanly(client: HedwigClient) -> None:
    terminal = Terminal()  # Ctrl-D straight away
    assert run_chat(client, session_id=None, read=terminal.read, write=terminal.write) == 0


def test_new_starts_another_conversation(client: HedwigClient) -> None:
    terminal = Terminal("/new", "/quit")
    run_chat(client, session_id=None, read=terminal.read, write=terminal.write)

    opened = [line for line in terminal.said("info") if line.startswith("New conversation")]
    assert len(opened) == 1


def test_a_model_outage_is_survivable(client: HedwigClient, llm_provider: RecordedProvider) -> None:
    """HEDWIG's own honest sentence is shown, the cause is explained, and the loop carries on
    so the user can try again once the model is back."""
    terminal = Terminal("Are you there?", "/quit")
    llm_provider.available = False

    code = run_chat(client, session_id=None, read=terminal.read, write=terminal.write)

    assert code == 0
    assert terminal.said("hedwig"), "the failed turn's reply was not shown"
    assert "language model is unavailable" in terminal.said("error")[0]


def test_a_refused_token_ends_the_chat(http: TestClient) -> None:
    terminal = Terminal("hello")
    client = HedwigClient("http://testserver", "wrong", http=http)
    assert run_chat(client, session_id=None, read=terminal.read, write=terminal.write) == 1
    assert "token was refused" in terminal.said("error")[0]


def test_a_stopped_backend_says_how_to_start_it() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.Client(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(refuse))
    terminal = Terminal()
    code = run_chat(
        HedwigClient("http://127.0.0.1:1", "t", http=http),
        session_id=None,
        read=terminal.read,
        write=terminal.write,
    )
    assert code == 1
    assert "hedwig serve" in terminal.said("error")[0]


def test_a_non_envelope_error_is_still_an_error() -> None:
    http = httpx.Client(
        base_url="http://x",
        transport=httpx.MockTransport(lambda _: httpx.Response(502, text="Bad Gateway")),
    )
    terminal = Terminal()
    assert (
        run_chat(
            HedwigClient("http://x", "t", http=http),
            session_id=None,
            read=terminal.read,
            write=terminal.write,
        )
        == 1
    )
    assert terminal.said("error")[0].startswith("internal:")


# -- hedwig token -------------------------------------------------------------------


def test_token_show_and_rotate(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"HEDWIG_RUNTIME__DATA_DIR": str(tmp_path)}

    shown = runner.invoke(app, ["token", "show"], env=env)
    assert shown.exit_code == 0, shown.output
    token = shown.stdout.strip()
    assert token == TokenFile(tmp_path / "token").read()
    assert runner.invoke(app, ["token", "show"], env=env).stdout.strip() == token

    rotated = runner.invoke(app, ["token", "rotate"], env=env)
    assert rotated.exit_code == 0
    assert TokenFile(tmp_path / "token").read() not in (None, token)


def test_chat_without_a_token_explains_what_to_do(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["chat"], env={"HEDWIG_RUNTIME__DATA_DIR": str(tmp_path)})
    assert result.exit_code == 1
    assert "hedwig serve" in result.output
