"""Reading the web, carefully (docs/13 §13).

Layers 1-3 of the injection defence (docs/13 §6) live here: what may be fetched, how it is
turned into text, and how suspicious that text is. Nothing here decides *whether* to research —
that is the planner's — and nothing here stores anything but the egress audit.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.store import Database
from hedwig.knowledge.injection import Scan, normalise, scan

logger = get_logger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; HEDWIG-local/0.1; personal research assistant)"
"""Generic on purpose: nothing about the user, their machine or their name (docs/13 §13)."""

HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
"""Sent on every request, whichever client carries it."""

SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_BYTES = 1_000_000
EXCERPT_CHARS = 2_000
SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "head"})


class WebRefusedError(Exception):
    """A fetch the controls would not make. The message says why, for the tool output."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    title: str
    text: str
    scan: Scan


Resolver = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


@dataclass(slots=True)
class WebClient:
    database: Database
    clock: Clock
    http: httpx.AsyncClient | None = None
    resolver: Resolver = _resolve
    search_endpoint: str = SEARCH_ENDPOINT
    timeout: float = 10.0
    _robots: dict[str, asyncio.Task[RobotFileParser | None]] = field(default_factory=dict)
    """One fetch of robots.txt per site, shared by concurrent reads of that site."""

    def _client(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                headers=HEADERS,
            )
        return self.http

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()

    # -- search ------------------------------------------------------------------

    async def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        text = " ".join(query.split())[:200]
        try:
            response = await self._client().post(
                self.search_endpoint, data={"q": text}, headers=HEADERS
            )
        except httpx.HTTPError as error:
            self._log(
                "search",
                "POST",
                self.search_endpoint,
                query=text,
                outcome="error",
                detail=str(error),
            )
            raise WebRefusedError(
                f"the search engine could not be reached ({type(error).__name__})"
            ) from error
        self._log(
            "search",
            "POST",
            self.search_endpoint,
            query=text,
            status=response.status_code,
            size=len(response.content),
            outcome="ok" if response.is_success else "error",
        )
        if not response.is_success:
            raise WebRefusedError(f"the search engine answered {response.status_code}")
        return parse_results(response.text)[:limit]

    # -- reading -----------------------------------------------------------------

    async def read(self, url: str) -> Page:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            self._log("read", "GET", url, outcome="refused", detail="scheme")
            raise WebRefusedError("only http and https pages can be read")
        await self._check_host(parsed.hostname, url)
        if not await self._allowed_by_robots(parsed.scheme, parsed.hostname, url):
            self._log("read", "GET", url, outcome="disallowed", detail="robots.txt")
            raise WebRefusedError("the site's robots.txt asks not to be read")

        try:
            async with self._client().stream("GET", url, headers=HEADERS) as response:
                kind = response.headers.get("content-type", "")
                if not any(t in kind for t in ("text/html", "text/plain", "application/xhtml")):
                    self._log(
                        "read",
                        "GET",
                        url,
                        status=response.status_code,
                        outcome="refused",
                        detail=kind,
                    )
                    raise WebRefusedError(f"not a readable page ({kind or 'unknown type'})")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                status = response.status_code
                encoding = response.encoding or "utf-8"
        except httpx.HTTPError as error:
            self._log("read", "GET", url, outcome="error", detail=str(error))
            raise WebRefusedError(
                f"the page could not be fetched ({type(error).__name__})"
            ) from error

        if status >= 400:
            self._log("read", "GET", url, status=status, size=len(body), outcome="error")
            raise WebRefusedError(f"the page answered {status}")

        title, text = extract_text(bytes(body[:MAX_BYTES]).decode(encoding, errors="replace"))
        result = scan(text)
        self._log(
            "read",
            "GET",
            url,
            status=status,
            size=len(body),
            outcome="quarantined" if result.quarantined else "ok",
            detail=",".join(result.flags) or None,
        )
        return Page(url=url, title=title, text=text[:EXCERPT_CHARS], scan=result)

    async def _check_host(self, host: str, url: str) -> None:
        """No reaching into the user's own machine or network (docs/13 §13, layer 1)."""
        lowered = host.lower().rstrip(".")
        if lowered == "localhost" or lowered.endswith(
            (".local", ".internal", ".localhost", ".lan")
        ):
            self._log("read", "GET", url, outcome="refused", detail="local host")
            raise WebRefusedError("local addresses are never read")
        try:
            addresses = await asyncio.to_thread(self.resolver, lowered)
        except OSError as error:
            self._log("read", "GET", url, outcome="error", detail="dns")
            raise WebRefusedError("the site's name does not resolve") from error
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%")[0])
            if not ip.is_global:
                self._log("read", "GET", url, outcome="refused", detail=f"non-public address {ip}")
                raise WebRefusedError("that address is not on the public internet")

    async def _allowed_by_robots(self, scheme: str, host: str, url: str) -> bool:
        key = f"{scheme}://{host}"
        if key not in self._robots:
            self._robots[key] = asyncio.create_task(self._fetch_robots(key))
        parser = await self._robots[key]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    async def _fetch_robots(self, site: str) -> RobotFileParser | None:
        robots_url = f"{site}/robots.txt"
        try:
            response = await self._client().get(robots_url, headers=HEADERS)
        except httpx.HTTPError:
            self._log("robots", "GET", robots_url, outcome="error")
            return None
        self._log(
            "robots",
            "GET",
            robots_url,
            status=response.status_code,
            size=len(response.content),
            outcome="ok",
        )
        if response.status_code >= 400:
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    # -- audit -------------------------------------------------------------------

    def _log(
        self,
        purpose: str,
        method: str,
        url: str,
        *,
        outcome: str,
        query: str | None = None,
        status: int | None = None,
        size: int | None = None,
        detail: str | None = None,
    ) -> None:
        try:
            self.database.execute(
                "INSERT INTO egress_log (id, purpose, method, host, url, query, status, bytes, "
                "outcome, detail, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id("egr"),
                    purpose,
                    method,
                    urlparse(url).hostname or "",
                    url[:2000],
                    query,
                    status,
                    size,
                    outcome,
                    (detail or "")[:500] or None,
                    self.clock.now().isoformat(timespec="milliseconds"),
                ),
            )
        except Exception:  # pragma: no cover - the audit must not break a fetch
            logger.exception("could not record egress", extra=fields(url=url))


# -- parsing -----------------------------------------------------------------------


class _Text(HTMLParser):
    """Visible text and the title. Hidden markup goes, which is where injections like to hide."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        hidden = "hidden" in attributes or "display:none" in (
            attributes.get("style") or ""
        ).replace(" ", "")
        if tag in SKIP_TAGS or hidden or attributes.get("aria-hidden") == "true":
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        elif not self._skip:
            self.parts.append(data)


def extract_text(html: str) -> tuple[str, str]:
    parser = _Text()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in normalise("".join(parser.parts)).splitlines())
    text = "\n".join(line for line in lines if line)
    return " ".join("".join(parser.title).split()), text


class _Results(HTMLParser):
    """DuckDuckGo's HTML results: `a.result__a` for the link, `.result__snippet` for the text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.results.append(
                {"url": _unwrap(attributes.get("href") or ""), "title": "", "snippet": ""}
            )
            self._field = "title"
        elif "result__snippet" in classes and self.results:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "td", "div"}:
            self._field = None

    def handle_data(self, data: str) -> None:
        if self._field and self.results:
            self.results[-1][self._field] += data


def _unwrap(href: str) -> str:
    """DuckDuckGo links through a redirect: `//duckduckgo.com/l/?uddg=<the real url>`."""
    if "uddg=" in href:
        target = parse_qs(urlparse(href if "://" in href else f"https:{href}").query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def parse_results(html: str) -> list[SearchResult]:
    parser = _Results()
    parser.feed(html)
    found: list[SearchResult] = []
    for item in parser.results:
        url = item["url"]
        if not url.startswith(("http://", "https://")) or "duckduckgo.com/y.js" in url:
            continue  # ads and internal links
        found.append(
            SearchResult(
                title=" ".join(item["title"].split()),
                url=url,
                snippet=" ".join(normalise(item["snippet"]).split()),
            )
        )
    return found
