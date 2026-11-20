"""The user's answers to the findings feed (docs/11 §7, §13)."""

from __future__ import annotations

from hedwig.core.errors import NotFoundError
from hedwig.core.types import TrustTier
from hedwig.curiosity.store import CuriosityStore, Finding
from hedwig.memory.capture import BeliefCandidate, CaptureService, Verdict


async def confirm(store: CuriosityStore, capture: CaptureService, finding_id: str) -> Finding:
    """Keep a finding as a belief — tentative, and still untrusted (docs/11 §5.1).

    The user vouching for it makes it worth remembering; it does not make the web the user's
    word. Promotion beyond tentative needs corroboration (T2).
    """
    finding = await store.finding(finding_id)
    if finding is None:
        raise NotFoundError("No such finding.", finding_id=finding_id)
    report = await capture.capture(
        beliefs=(
            BeliefCandidate(
                statement=finding.summary,
                confidence=0.4,
                trust=TrustTier.UNTRUSTED,
                source_ref=finding.sources[0]["url"] if finding.sources else None,
            ),
        )
    )
    stored = next(
        (o.memory_id for o in report.outcomes if o.verdict is Verdict.STORED and o.memory_id),
        None,
    )
    return await store.set_finding(finding_id, status="confirmed", belief_id=stored)


async def dismiss(store: CuriosityStore, finding_id: str) -> Finding:
    """Not interested. Similar findings are discarded from now on (docs/11 §7)."""
    return await store.set_finding(finding_id, status="dismissed")
