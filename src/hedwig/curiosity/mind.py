"""Deferred surfacing: a finding offered to a turn that might welcome it (docs/11 §7, §13).

At most one finding per turn, two a day, an hour apart, and only when the conversation opens or
touches the finding's subject. Marked surfaced when offered; whether HEDWIG mentions it is the
reply's judgement, guided by the label.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

from hedwig.core.ports import Clock
from hedwig.core.ports.brain import MindReader, MindSnapshot
from hedwig.curiosity.store import CuriosityStore, Finding
from hedwig.knowledge.cues import is_greeting
from hedwig.memory.capture import similarity

SHARE_DIRECTIVE = (
    "They have just greeted you. After greeting them back, briefly share the one thing you "
    "found while exploring, in a sentence or two, as something you read."
)
"""Vision Example B. Offered on a greeting, "mention it if it fits" read as optional to a 2B
model, which said only "Hello." (docs/11 §13). A greeting is the moment; say so — through
cognition's own channel, not inside the untrusted block."""

MAX_DAILY = 2
MIN_GAP = timedelta(hours=1)
RELATED = 0.15


@dataclass(slots=True)
class FindingsMindReader:
    inner: MindReader
    store: CuriosityStore
    clock: Clock

    async def snapshot(self, *, input: str = "") -> MindSnapshot:  # noqa: A002
        snapshot = await self.inner.snapshot(input=input)
        finding = await self._offer(input)
        if finding is None:
            return snapshot
        await self.store.set_finding(finding.id, status="surfaced")
        directives = snapshot.directives
        if is_greeting(input):
            # First: the moment is the point of this turn (docs/11 §13).
            directives = (SHARE_DIRECTIVE, *directives)
        return replace(snapshot, findings=(render(finding),), directives=directives)

    async def _offer(self, text: str) -> Finding | None:
        now = self.clock.now()
        recent = self.store.surfaced_since(now - timedelta(days=1))
        if len(recent) >= MAX_DAILY or any(now - at < MIN_GAP for at in recent):
            return None
        for finding in await self.store.findings(statuses=("new",), limit=10):
            subject = f"{finding.question} {finding.summary}"
            if is_greeting(text) or similarity(text, subject) >= RELATED:
                return finding
        return None


def render(finding: Finding) -> str:
    links = ", ".join(source["url"] for source in finding.sources[:3])
    about = f"On “{finding.question}”: " if finding.question else ""
    return f"{about}{finding.summary}" + (f" (sources: {links})" if links else "")
