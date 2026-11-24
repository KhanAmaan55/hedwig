"""Corrections in conversation (docs/06 §19, MEM-07).

"No, she lives in Lisbon" — after the turn, if the message sounds like a correction and HEDWIG
was just using a belief it might be about, the utility model proposes how that belief should
read. Checked deterministically, then put to the user. Nothing changes without their yes; a
yes is §18's correction.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from hedwig.core.errors import ConflictError, HedwigError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.event_bus import Event
from hedwig.core.ports.llm import (
    GenerationRequest,
    GenerationResult,
    Message,
    RequestPriority,
    Role,
)
from hedwig.core.ports.memory import Belief, BeliefStatus
from hedwig.core.presence import Presence
from hedwig.core.store import Database
from hedwig.core.types import MemoryId
from hedwig.memory.store import SqliteMemoryStore

logger = get_logger(__name__)

SUBSCRIPTION = "memory.corrections"
PURPOSE = "extract.correction"
TIMEOUT_SECONDS = 120.0
"""One small model call; generous for a cold model on a laptop CPU."""
MAX_CANDIDATES = 8

# -- the rule gate ---------------------------------------------------------------------------

CORRECTION_PATTERNS = (
    # "No, she lives in Lisbon." · "Nope — it's July." · "Actually, Ana lives in Lisbon now."
    re.compile(
        r"^\s*(?:no|nope|nah|actually|wrong|not quite|not really)\b[\s,.!:;\u2014\u2013-]+(?P<rest>.+)",
        re.I,
    ),
    # "That's not right" · "you've got that wrong" · "that isn't true"
    re.compile(
        r"\b(?:that(?:'s| is| was)?|you(?:'re| are)|you(?:'ve| have) got (?:it|that))\s+"
        r"(?:not (?:right|true|correct|accurate)|wrong|incorrect|mistaken|isn'?t (?:right|true))\b",
        re.I,
    ),
    re.compile(r"^\s*correction\b", re.I),
    re.compile(r"\b(?:no longer|not any ?more|anymore)\b", re.I),
    # "Not Porto, Lisbon." · "Lisbon, not Porto."
    re.compile(
        r"(?:^|[.;]\s*)not\s+[\w'-]+(?:\s+[\w'-]+)?\s*[,;:\u2014\u2013-]+\s*(?:but\s+)?[\w'-]+",
        re.I,
    ),
    re.compile(r"\b[\w'-]+\s*,\s*not\s+[\w'-]+\s*[.!]?\s*$", re.I),
    # "I'm vegetarian, not vegan" handled above; "I'm not vegan, I'm vegetarian"
    re.compile(r"\b(?:i'?m|i am|she'?s|he'?s|it'?s|we'?re|they'?re)\s+not\s+\w+[^.?!]*,", re.I),
)
NOT_A_CORRECTION = re.compile(
    r"^\s*(?:no|nope|nah|actually)\b[\s,.!]*(?:thanks?|thank you|problem|worries|need|"
    r"that'?s (?:fine|all|ok|okay|it)|it'?s fine|i'?m (?:good|fine|ok)|nothing|not now|"
    r"never ?mind|i was wondering|can you|could you|would you|what|how|why|who|when|where|"
    r"tell me|let'?s|i (?:want|wanted|was|think|need|have a question))\b",
    re.I,
)


def sounds_like_correction(text: str) -> bool:
    """Whether a message may be correcting something (docs/06 §19). Listed, so it is tested."""
    words = text.split()
    if len(words) < 3 or text.rstrip().endswith("?") or NOT_A_CORRECTION.search(text):
        return False
    return any(pattern.search(text) for pattern in CORRECTION_PATTERNS)


# -- the checks ------------------------------------------------------------------------------

STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "from",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "their",
        "there",
        "they",
        "them",
        "he",
        "she",
        "his",
        "her",
        "him",
        "user",
        "user's",
        "users",
        "you",
        "your",
        "i",
        "me",
        "my",
        "we",
        "our",
        "us",
        "as",
        "so",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "will",
        "would",
        "can",
        "could",
        "just",
        "also",
        "very",
        "now",
        "who",
        "which",
        "what",
        "when",
        "where",
        "how",
        "than",
        "then",
        "into",
    ]
)
NEGATION = frozenset(
    [
        "no",
        "not",
        "never",
        "longer",
        "anymore",
        "any",
        "more",
        "doesn't",
        "don't",
        "isn't",
        "aren't",
        "wasn't",
        "weren't",
        "didn't",
        "does",
        "do",
        "did",
        "no-longer",
        "instead",
        "rather",
        "actually",
        "now",
    ]
)


def content_words(text: str) -> set[str]:
    """Lower-cased content words, plurals folded ("compilers" is the compiler)."""
    words = {w.removesuffix("'s") for w in re.findall(r"[a-z0-9']+", text.lower())}
    return {
        w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
        for w in words
        if w not in STOP and len(w) > 1
    }


def acceptable(old: str, new: str, said: str) -> str | None:
    """Why a proposed rewrite is refused, or None when it may be put to the user."""
    if " ".join(new.split()).lower() == " ".join(old.split()).lower():
        return "unchanged"
    before, after, spoken = content_words(old), content_words(new), content_words(said)
    named = before & spoken  # the user mentioned what the belief is about
    if not (before & after or named):
        return "different_subject"
    added = after - before - NEGATION
    if added - spoken:
        return "words_the_user_did_not_say"
    if not named and not added:
        # Measured live: told "No, I meant the other book", a 2B model negates whichever
        # belief it was shown ("The user's sister Ana does not live in Porto"). A bare
        # negation needs the user to have named the thing; a replacement needs their word.
        return "nothing_said_about_it"
    return None


# -- the proposer ----------------------------------------------------------------------------

INSTRUCTIONS = (
    "You check whether the user is correcting something HEDWIG believes about them.\n"
    "You get numbered beliefs and the user's latest message. If the message says one of the "
    "beliefs is wrong or no longer true, answer with that belief's number and the belief "
    "rewritten so it is true according to the user — the same third-person style, about "
    '"the user", using the user\'s own words for what changed and nothing else. If the '
    "message does not correct any of them, answer 0 and an empty string."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "belief": {"type": "integer", "minimum": 0, "maximum": MAX_CANDIDATES},
        "corrected": {"type": "string", "maxLength": 300},
    },
    "required": ["belief", "corrected"],
}


class Generator(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


class CorrectionProposer:
    def __init__(
        self,
        database: Database,
        *,
        store: SqliteMemoryStore,
        gateway: Generator,
        clock: Clock,
        presence: Presence | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._db = database
        self._store = store
        self._gateway = gateway
        self._clock = clock
        self._presence = presence
        self._bus = bus
        self._previous: dict[str, tuple[str, ...]] = {}
        """Per session, the previous turn's recalled ids: what HEDWIG's last reply stood on."""
        self._subscribed = False

    # -- lifecycle -----------------------------------------------------------------------------

    async def start(self) -> None:
        if self._bus is not None and not self._subscribed:
            self._bus.subscribe(
                "conversation.turn.completed",
                self.handle,
                name=SUBSCRIPTION,
                timeout=TIMEOUT_SECONDS,
            )
            self._subscribed = True

    async def stop(self) -> None:
        return None

    async def health(self) -> Health:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM correction_proposal WHERE status = 'proposed'"
        )
        return Health(status=HealthStatus.OK, detail={"waiting": int(row["n"]) if row else 0})

    async def handle(self, event: Event) -> None:
        payload = event.payload
        session_id = str(payload.get("session_id") or "")
        recalled = tuple(str(i) for i in payload.get("recalled_memory_ids") or ())
        earlier = self._previous.get(session_id, ())
        self._previous[session_id] = recalled
        if str(payload.get("status")) not in ("completed", "truncated"):
            return
        await self.consider(
            str(payload.get("input") or ""),
            session_id=session_id,
            turn_id=str(payload.get("turn_id") or ""),
            recalled=(*recalled, *earlier),
        )

    # -- the proposal --------------------------------------------------------------------------

    async def consider(
        self, said: str, *, session_id: str, turn_id: str, recalled: Sequence[str]
    ) -> dict[str, Any] | None:
        """Propose a correction if `said` corrects one of the recalled beliefs."""
        if not sounds_like_correction(said):
            return None
        candidates = await self._candidates(recalled)
        if not candidates:
            return None
        answer = await self._ask(said, candidates)
        if answer is None:
            return None
        number, corrected = answer
        if not 1 <= number <= len(candidates):
            return None
        old = candidates[number - 1]
        corrected = " ".join(corrected.split())
        refused = acceptable(old.statement, corrected, said)
        if refused:
            logger.info("correction not proposed", extra=fields(reason=refused, turn=turn_id))
            return None
        return await self._propose(old, corrected, session_id=session_id, turn_id=turn_id)

    async def _candidates(self, recalled: Sequence[str]) -> list[Belief]:
        found: list[Belief] = []
        for memory_id in dict.fromkeys(recalled):
            item = await self._store.inspect(MemoryId(memory_id))
            if (
                isinstance(item, Belief)
                and item.tombstoned_at is None
                and item.status in (BeliefStatus.ACTIVE, BeliefStatus.TENTATIVE)
            ):
                found.append(item)
            if len(found) >= MAX_CANDIDATES:
                break
        return found

    async def _ask(self, said: str, candidates: Sequence[Belief]) -> tuple[int, str] | None:
        listing = "\n".join(f"{i + 1}. {belief.statement}" for i, belief in enumerate(candidates))
        request = GenerationRequest(
            messages=(
                Message(role=Role.SYSTEM, content=INSTRUCTIONS),
                Message(
                    role=Role.USER,
                    content=f"[beliefs]\n{listing}\n[the user's message]\n{said}\n[end]",
                ),
            ),
            purpose=PURPOSE,
            json_schema=SCHEMA,
            max_tokens=200,
            temperature=0.0,
            priority=RequestPriority.BACKGROUND,
        )
        try:
            result = await self._gateway.generate(request)
        except HedwigError as error:
            logger.warning("correction check skipped", extra=fields(error=str(error)))
            return None
        parsed = result.parsed
        if not isinstance(parsed, dict):
            return None
        try:
            return int(parsed.get("belief") or 0), str(parsed.get("corrected") or "")
        except (TypeError, ValueError):
            return None

    async def _propose(
        self, old: Belief, corrected: str, *, session_id: str, turn_id: str
    ) -> dict[str, Any] | None:
        duplicate = self._db.query_one(
            "SELECT id FROM correction_proposal WHERE memory_id = ? AND proposed_text = ? "
            "AND status = 'proposed'",
            (old.id, corrected),
        )
        if duplicate is not None:
            return None
        proposal_id = new_id("cor")
        now = self._now()
        self._db.execute(
            "INSERT INTO correction_proposal (id, memory_id, proposed_text, session_id, turn_id, "
            "created_at) VALUES (?,?,?,?,?,?)",
            (proposal_id, old.id, corrected, session_id or None, turn_id or None, now),
        )
        proposal = self.proposal(proposal_id)
        assert proposal is not None
        if self._bus is not None:
            await self._bus.emit(
                "memory.correction.proposed",
                {"proposal_id": proposal_id, "memory_id": str(old.id)},
                source="memory",
            )
        if self._presence is not None:
            await self._presence.broadcast("memory.correction.proposed", proposal)
        logger.info("correction proposed", extra=fields(proposal=proposal_id, memory=old.id))
        return proposal

    # -- the user's answer -----------------------------------------------------------------------

    def proposals(
        self, *, status: str | None = "proposed", session_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        sql = f"{_SELECT} WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND p.status = ?"
            args.append(status)
        if session_id:
            sql += " AND p.session_id = ?"
            args.append(session_id)
        rows = self._db.query(f"{sql} ORDER BY p.created_at DESC LIMIT ?", (*args, limit))
        return [dict(r) for r in rows]

    def proposal(self, proposal_id: str) -> dict[str, Any] | None:
        row = self._db.query_one(f"{_SELECT} WHERE p.id = ?", (proposal_id,))
        return dict(row) if row else None

    async def accept(self, proposal_id: str) -> dict[str, Any]:
        """Yes: §18's correction, in the words the user agreed to."""
        proposal = self._pending(proposal_id)
        try:
            replacement = await self._store.correct(
                MemoryId(proposal["memory_id"]), text=proposal["proposed_text"]
            )
        except (ConflictError, NotFoundError):
            # Replaced, forgotten or erased since: the proposal is about something gone.
            self._settle(proposal_id, "stale")
            raise
        self._settle(proposal_id, "accepted", replacement=str(replacement))
        return self.proposal(proposal_id) or {}

    async def decline(self, proposal_id: str) -> dict[str, Any]:
        self._pending(proposal_id)
        self._settle(proposal_id, "declined")
        return self.proposal(proposal_id) or {}

    def _pending(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.proposal(proposal_id)
        if proposal is None:
            raise NotFoundError("No such proposal.", proposal_id=proposal_id)
        if proposal["status"] != "proposed":
            raise ConflictError(
                f"That proposal was already {proposal['status']}.", proposal_id=proposal_id
            )
        return proposal

    def _settle(self, proposal_id: str, status: str, *, replacement: str | None = None) -> None:
        self._db.execute(
            "UPDATE correction_proposal SET status = ?, settled_at = ?, replacement_id = ? "
            "WHERE id = ?",
            (status, self._now(), replacement, proposal_id),
        )

    def _now(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")


_SELECT = (
    "SELECT p.id, p.memory_id, b.statement AS current_text, p.proposed_text, p.session_id, "
    "p.turn_id, p.status, p.replacement_id, p.created_at, p.settled_at "
    "FROM correction_proposal p LEFT JOIN belief b ON b.id = p.memory_id"
)
