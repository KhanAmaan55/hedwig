"""The planner (docs/07 §3.1, node `deliberate`).

Decides what a turn should do: answer, use a tool, ask for clarification, or decline.

This implementation is **rules, not a model** — deliberately, and not only because
Milestone 4 forbids connecting Ollama. A deterministic planner is worth having on its own
terms: it is exhaustively testable, it costs nothing, it cannot hallucinate a tool that
does not exist, and it gives the LLM-backed planner that replaces it a baseline to be
measured against. Where it is genuinely unsure it says so, which is what `CLARIFY` is for.

The rules are ordered most-specific first, and each one states the situation it recognises.
Nothing here reads memory, calls a model, or knows what a tool does beyond its name.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.brain import (
    Intent,
    Plan,
    RecalledContext,
    ToolOutcome,
    ToolRequest,
    TurnPolicy,
)
from hedwig.knowledge.cues import needs_web, refers_to_aims

logger = get_logger(__name__)

TIME_PATTERN = re.compile(r"\b(what time|what's the time|today's date|what date)\b", re.I)
CALC_PATTERN = re.compile(r"(?:^|\s)(\d+(?:\.\d+)?\s*[-+*/]\s*\d+(?:\.\d+)?)")
RECALL_PATTERN = re.compile(
    r"\b(remember|recall|last time|earlier|we (?:discuss|talk|spoke|said)\w*|you said|"
    r"previously|before)\b",
    re.I,
)
MAX_GOAL_QUERIES = 3


def goal_subject(goal: str) -> str:
    """A goal line as the snapshot renders it, reduced to what it is about:
    "Help the user run the Lisbon half marathon (40% done); lately: …" → "run the Lisbon half
    marathon"."""
    subject = goal.split(" (", 1)[0].split(";", 1)[0].strip()
    for prefix in ("Help the user ", "help the user "):
        subject = subject.removeprefix(prefix)
    return subject


NO_WEB_DIRECTIVE = (
    "This asks for current information, and web search is switched off. Say plainly that you "
    "cannot look it up right now, answer from what you know, and say it may be out of date."
)
CITE_DIRECTIVE = (
    "Part of what you know here was researched on the web just now. Use it, cite sources by "
    "number like [1] with the link, and say plainly what the sources do not settle."
)
MIN_QUESTION_WORDS = 2


@dataclass(frozen=True, slots=True)
class RulePlanner:
    """A deterministic `Planner`.

    `available_tools` bounds what it may ask for. A planner that can only name tools that
    exist cannot produce a request the runner has to reject — which is a whole class of
    failure removed rather than handled.
    """

    available_tools: frozenset[str] = frozenset({"get_datetime", "calculate", "search_memory"})

    async def deliberate(
        self,
        *,
        text: str,
        context: RecalledContext,
        policy: TurnPolicy,
        outcomes: Sequence[ToolOutcome],
        iteration: int,
    ) -> Plan:
        stripped = text.strip()

        # 1. Nothing to work with.
        if not stripped:
            return Plan(
                intent=Intent.CLARIFY,
                rationale="the message was empty",
                confidence=1.0,
            )

        # 2. A tool already answered. Two rounds of the same tool is a loop, not a plan.
        if outcomes:
            return self._after_tools(stripped, outcomes)

        # 3. Tools are disabled for this turn — by policy, not by the planner's judgement.
        if not policy.allow_tools:
            return Plan(
                intent=Intent.ANSWER,
                rationale="tools are disabled for this turn",
                confidence=0.6,
            )

        # 4. A recognised, bounded request that a tool answers exactly.
        if tool := self._tool_for(stripped):
            return Plan(
                intent=Intent.ACT,
                rationale=f"{tool.name} answers this directly",
                tool=tool,
                confidence=0.9,
            )

        # 5. Current information, with the web switched off: be honest about it.
        if needs_web(stripped) and "research" not in self.available_tools:
            return Plan(
                intent=Intent.ANSWER,
                rationale="needs current information; web search is off",
                confidence=0.4,
                directives=(NO_WEB_DIRECTIVE,),
            )

        # 6. Too little to go on. Asking beats guessing.
        if len(stripped.split()) < MIN_QUESTION_WORDS and stripped.endswith("?"):
            return Plan(
                intent=Intent.CLARIFY,
                rationale="the question is too short to interpret",
                confidence=0.7,
            )

        return Plan(
            intent=Intent.ANSWER,
            rationale="answering from what is already known",
            confidence=0.5 + 0.2 * bool(context.items),
        )

    def _tool_for(self, text: str) -> ToolRequest | None:
        if TIME_PATTERN.search(text) and "get_datetime" in self.available_tools:
            return ToolRequest(name="get_datetime", reason="the request is about the clock")

        if (match := CALC_PATTERN.search(text)) and "calculate" in self.available_tools:
            return ToolRequest(
                name="calculate",
                arguments={"expression": match.group(1).strip()},
                reason="the request contains an arithmetic expression",
            )

        if needs_web(text) and "research" in self.available_tools:
            return ToolRequest(
                name="research",
                arguments={"query": text[:300]},
                reason="the question needs current information",
            )

        if RECALL_PATTERN.search(text) and "search_memory" in self.available_tools:
            return ToolRequest(
                name="search_memory",
                arguments={"query": text},
                reason="the request refers to something from before",
            )

        return None

    def _after_tools(self, text: str, outcomes: Sequence[ToolOutcome]) -> Plan:
        """What to do once tools have run.

        A failed tool is not a failed turn: compose around it and say so. That is better
        behaviour than an error, and it is why `act` returns outcomes rather than raising
        (docs/07 §8).
        """
        failures = [outcome for outcome in outcomes if not outcome.ok]
        if failures and len(failures) == len(outcomes):
            return Plan(
                intent=Intent.ANSWER,
                rationale=f"every tool failed ({failures[0].error}); answering without them",
                confidence=0.3,
            )
        researched = any(o.name == "research" and o.ok for o in outcomes)
        return Plan(
            intent=Intent.ANSWER,
            rationale=f"{len(outcomes)} tool result(s) available",
            confidence=0.8,
            directives=(CITE_DIRECTIVE,) if researched else (),
        )

    # -- query planning ----------------------------------------------------

    async def plan_queries(
        self, text: str, *, policy: TurnPolicy, goals: Sequence[str] = ()
    ) -> Sequence[str]:
        """Expand the input into retrieval queries (docs/06 §5.2).

        One query against one index is keyword search, not retrieval. The real expansion —
        literal, entity-scoped, hypothetical-answer, goal-scoped — needs a model and a
        memory store. This produces the literal query and, when the text plainly refers to
        the past, a recall-flavoured one, so the shape is right for what replaces it.
        """
        stripped = text.strip()
        if not stripped:
            return ()

        queries = [stripped]
        if RECALL_PATTERN.search(stripped):
            queries.append(RECALL_PATTERN.sub("", stripped).strip() or stripped)
        if goals and refers_to_aims(stripped):
            # "Am I on track?" names no goal, so no word of it matches the marathon. The goals
            # it is about become queries of their own (docs/06 §20).
            queries += [goal_subject(goal) for goal in goals[:MAX_GOAL_QUERIES]]

        logger.debug("queries planned", extra=fields(count=len(queries)))
        return tuple(dict.fromkeys(queries))  # de-duplicated, order preserved
