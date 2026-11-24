"""Speaking first (docs/11 §7 mode 3, §14; ADR-0026).

Every few minutes: if every gate holds, pick the one thing most worth saying — a finding the
user asked for or that serves one of their goals, else a check-in on a goal's open question —
and have the brain say it into the conversation the app is showing. At most one a day.

Decides *what* and *when*. The brain writes the message; presence delivers it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from hedwig.core.errors import NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, ConversationHistory, EventBus
from hedwig.core.ports.goals import Goal, GoalKind, GoalNote, NoteKind
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.core.presence import Presence
from hedwig.core.store import Database
from hedwig.core.types import TrustTier
from hedwig.curiosity.store import CuriosityStore, Finding
from hedwig.goals.store import telling_words

logger = get_logger(__name__)

FINDING_SPACING = timedelta(hours=1)
"""After a finding was surfaced into a turn, wait this long (docs/11 §7: ≤1 an hour)."""
QUESTION_AGE = timedelta(days=3)
"""An open question younger than this may still be settled in conversation."""
CHECK_IN_GAP = timedelta(days=7)
"""At most one check-in a week on any one goal."""


class SpeaksFirst(Protocol):
    """What the brain offers: `Brain.speak_first`."""

    async def speak_first(
        self,
        text: str,
        *,
        session_id: str | None,
        trust: TrustTier,
        meta: dict[str, Any],
        channel: str = "web",
    ) -> tuple[str, str]: ...

    @property
    def busy(self) -> bool: ...


class Goals(Protocol):
    async def active(self, *, limit: int = 12) -> Sequence[Goal]: ...
    async def notes(self, goal_id: str, *, limit: int = 20) -> Sequence[GoalNote]: ...


@dataclass(frozen=True, slots=True)
class Offer:
    kind: str
    ref_id: str
    text: str
    trust: TrustTier


@dataclass(slots=True)
class Initiative:
    database: Database
    brain: SpeaksFirst
    presence: Presence
    curiosity: CuriosityStore
    goals: Goals
    history: ConversationHistory
    clock: Clock
    bus: EventBus | None = None
    enabled: bool = True
    max_daily: int = 1
    idle_minutes: float = 2.0
    quiet_start: int = 22
    quiet_end: int = 8
    timezone: str = ""
    last_reason: str = "not_checked"

    # -- the check ---------------------------------------------------------------------------

    async def check(self) -> dict[str, Any]:
        """Speak if every gate holds and there is something worth saying. Says why not."""
        reason = await self._blocked()
        offer = None if reason else await self._candidate()
        if offer is None:
            self.last_reason = reason or "nothing_to_say"
            return {"offered": False, "reason": self.last_reason}

        offer_id = new_id("ini")
        session_id, message_id = await self.brain.speak_first(
            offer.text,
            session_id=self.presence.current_session(),
            trust=offer.trust,
            meta={"initiative": {"offer_id": offer_id, "kind": offer.kind, "ref": offer.ref_id}},
        )
        now = self._iso(self.clock.now())
        self.database.execute(
            "INSERT INTO initiative_offer (id, kind, ref_id, session_id, message_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (offer_id, offer.kind, offer.ref_id, session_id, message_id, now),
        )
        if offer.kind == "finding":
            await self.curiosity.set_finding(offer.ref_id, status="surfaced")
        payload = {
            "offer_id": offer_id,
            "kind": offer.kind,
            "session_id": session_id,
            "message_id": message_id,
            "text": offer.text,
            "created_at": now,
        }
        delivered = await self.presence.broadcast("initiative.message", payload)
        if self.bus is not None:
            await self.bus.emit(
                "initiative.offer.made",
                {"offer_id": offer_id, "kind": offer.kind, "ref_id": offer.ref_id},
                source="initiative",
            )
        self.last_reason = "offered"
        logger.info(
            "spoke first", extra=fields(offer=offer_id, kind=offer.kind, delivered=delivered)
        )
        return {"offered": True, **payload}

    async def task(self, context: TaskContext) -> TaskResult:
        result = await self.check()
        return TaskResult(stats={"offered": result["offered"], "reason": result.get("reason")})

    async def _blocked(self) -> str | None:
        """The first gate that fails (docs/11 §7), or None when HEDWIG may speak."""
        now = self.clock.now()
        if not self.enabled or self.max_daily == 0:
            return "disabled"
        if not self.presence.present:
            return "nobody_here"
        if self._quiet(now):
            return "quiet_hours"
        if self.brain.busy or not await self._idle(now):
            return "conversation_in_progress"
        if self._count_since(now - timedelta(days=1)) >= self.max_daily:
            return "already_spoke_today"
        if any(
            now - at < FINDING_SPACING
            for at in self.curiosity.surfaced_since(now - FINDING_SPACING)
        ):
            return "finding_just_shared"
        return None

    async def _candidate(self) -> Offer | None:
        goals = [g for g in await self.goals.active() if g.kind is GoalKind.USER_STATED]
        return await self._finding(goals) or await self._check_in(goals)

    async def _finding(self, goals: Sequence[Goal]) -> Offer | None:
        """A new finding the user asked for, or one that shares a telling word with a goal."""
        for finding in await self.curiosity.findings(statuses=("new",), limit=20):
            gap = await self.curiosity.gap(finding.gap_id) if finding.gap_id else None
            if gap is not None and gap.origin == "user":
                return Offer("finding", finding.id, _asked(finding), TrustTier.UNTRUSTED)
            words = telling_words(f"{finding.question} {finding.summary}")
            for goal in goals:
                if words & telling_words(goal.description):
                    return Offer(
                        "finding", finding.id, _for_goal(finding, goal), TrustTier.UNTRUSTED
                    )
        return None

    async def _check_in(self, goals: Sequence[Goal]) -> Offer | None:
        """A goal whose latest journal entry is a question left open a few days ago."""
        now = self.clock.now()
        for goal in goals:
            if self._dismissed("goal_check_in", goal.id):
                continue
            if self._count_since(now - CHECK_IN_GAP, kind="goal_check_in", ref=goal.id):
                continue
            latest = next(
                (n for n in await self.goals.notes(goal.id, limit=5) if n.source != "system"),
                None,
            )
            if (
                latest
                and latest.kind is NoteKind.QUESTION
                and now - latest.created_at >= QUESTION_AGE
            ):
                question = latest.text.rstrip(".?")
                text = (
                    f"On “{yours(goal.description)}”: last time this was still open — "
                    f"{question[:1].lower()}{question[1:]}. Did you settle it?"
                )
                return Offer("goal_check_in", goal.id, text, TrustTier.SELF)
        return None

    # -- the user's side --------------------------------------------------------------------

    def offers(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Recent offers, newest first, each with whether the user answered it."""
        rows = self.database.query(
            "SELECT o.*, m.text, EXISTS (SELECT 1 FROM message a WHERE a.session_id = o.session_id "
            "AND a.role = 'user' AND a.seq > m.seq) AS answered "
            "FROM initiative_offer o LEFT JOIN message m ON m.id = o.message_id "
            "ORDER BY o.created_at DESC LIMIT ?",
            (limit,),
        )
        return [{**dict(r), "answered": bool(r["answered"])} for r in rows]

    async def dismiss(self, offer_id: str) -> dict[str, Any]:
        """*Not interested*: the finding is dismissed, or the goal gets no more check-ins."""
        row = self.database.query_one("SELECT * FROM initiative_offer WHERE id = ?", (offer_id,))
        if row is None:
            raise NotFoundError("No such offer.", offer_id=offer_id)
        self.database.execute(
            "UPDATE initiative_offer SET status = 'dismissed', dismissed_at = ? WHERE id = ?",
            (self._iso(self.clock.now()), offer_id),
        )
        if row["kind"] == "finding" and await self.curiosity.finding(row["ref_id"]) is not None:
            await self.curiosity.set_finding(row["ref_id"], status="dismissed")
        return {"dismissed": True, "kind": row["kind"]}

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "present": self.presence.present,
            "last_reason": self.last_reason,
            "offers": self.offers(),
        }

    # -- helpers ----------------------------------------------------------------------------

    def _count_since(
        self, since: datetime, *, kind: str | None = None, ref: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM initiative_offer WHERE created_at >= ?"
        args: list[Any] = [self._iso(since)]
        if kind:
            sql += " AND kind = ? AND ref_id = ?"
            args += [kind, ref]
        row = self.database.query_one(sql, tuple(args))
        return int(row["n"]) if row else 0

    def _dismissed(self, kind: str, ref: str) -> bool:
        return (
            self.database.query_one(
                "SELECT 1 FROM initiative_offer WHERE kind = ? AND ref_id = ? AND status = 'dismissed'",
                (kind, ref),
            )
            is not None
        )

    async def _idle(self, now: datetime) -> bool:
        recent = await self.history.list_sessions(limit=1)
        if not recent:
            return True
        last = datetime.fromisoformat(recent[0].last_active_at)
        return now - last >= timedelta(minutes=self.idle_minutes)

    def _quiet(self, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(self.timezone)) if self.timezone else now.astimezone()
        hour = local.hour
        if self.quiet_start > self.quiet_end:
            return hour >= self.quiet_start or hour < self.quiet_end
        return self.quiet_start <= hour < self.quiet_end

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat(timespec="milliseconds")


def _sources(finding: Finding) -> str:
    links = ", ".join(source["url"] for source in finding.sources[:3])
    return f" (sources: {links})" if links else ""


def _asked(finding: Finding) -> str:
    return (
        f"You asked me to find out “{finding.question}”. Here is what I read: "
        f"{finding.summary}{_sources(finding)}"
    )


def _for_goal(finding: Finding, goal: Goal) -> str:
    about = f" about “{finding.question}”" if finding.question else ""
    return (
        f"I read something{about} that might help with “{yours(goal.description)}”: "
        f"{finding.summary}{_sources(finding)}"
    )


def yours(description: str) -> str:
    """A goal as said to the user: "Help the user finish their compiler" → "finish your compiler"."""
    text = description.strip().rstrip(".")
    for prefix in ("Help the user ", "help the user "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    for third, second in (("the user's", "your"), ("their", "your"), ("the user", "you")):
        text = text.replace(f" {third} ", f" {second} ")
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text
