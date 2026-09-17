"""Gateway metrics (docs/19 §6).

Counters and histograms, in memory, labelled by tier, purpose and model. Deliberately small
and self-contained rather than a general metrics framework: there is exactly one consumer
today, and a dependency-free implementation is easier to reason about than a registry with
global state.

The histogram is a fixed-bucket cumulative one so the output is Prometheus-shaped without
needing a client library, and so quantiles do not require keeping every observation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DURATION_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
"""Seconds. Spread around the targets in docs/14 §3: <400 ms utility, <2 s first token."""


@dataclass(slots=True)
class Histogram:
    """Fixed-bucket cumulative histogram."""

    buckets: tuple[float, ...] = DURATION_BUCKETS
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    count: int = 0
    minimum: float = math.inf
    maximum: float = 0.0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self.total += value
        self.count += 1
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        for index, edge in enumerate(self.buckets):
            if value <= edge:
                self.counts[index] += 1
                return
        self.counts[-1] += 1

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count else 0.0

    def quantile(self, q: float) -> float:
        """Bucket-resolution estimate. Good enough to spot a regression, not a benchmark."""
        if not self.count:
            return 0.0
        target = q * self.count
        seen = 0
        for index, bucket_count in enumerate(self.counts):
            seen += bucket_count
            if seen >= target:
                return self.buckets[index] if index < len(self.buckets) else self.maximum
        return self.maximum

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean_s": round(self.mean, 4),
            "p50_s": round(self.quantile(0.5), 4),
            "p95_s": round(self.quantile(0.95), 4),
            "max_s": round(self.maximum, 4),
        }


@dataclass(slots=True)
class GatewayMetrics:
    """Everything the gateway counts.

    Named to match docs/19 §6 so the Prometheus output needs no translation layer.
    """

    requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    retries: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    schema_failures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    escalations: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tokens_in: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tokens_out: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    duration: dict[str, Histogram] = field(default_factory=dict)
    first_token: dict[str, Histogram] = field(default_factory=dict)
    model_switches: int = 0

    # -- recording ---------------------------------------------------------

    def record_success(
        self,
        *,
        purpose: str,
        tier: str,
        model: str,
        duration_ms: float,
        tokens_in: int,
        tokens_out: int,
        first_token_ms: float | None = None,
        attempts: int = 1,
    ) -> None:
        key = _key(purpose, tier, model)
        self.requests[key] += 1
        self.tokens_in[key] += tokens_in
        self.tokens_out[key] += tokens_out
        self._histogram(self.duration, key).observe(duration_ms / 1000)
        if first_token_ms is not None:
            self._histogram(self.first_token, key).observe(first_token_ms / 1000)
        if attempts > 1:
            self.retries[key] += attempts - 1

    def record_failure(self, *, purpose: str, tier: str, model: str, code: str) -> None:
        self.failures[f"{_key(purpose, tier, model)}|{code}"] += 1

    def record_schema_failure(self, *, purpose: str, tier: str, model: str) -> None:
        self.schema_failures[_key(purpose, tier, model)] += 1

    def record_escalation(self, *, purpose: str, tier: str, model: str) -> None:
        """A structured call that had to fall back to a larger tier.

        Recorded here rather than on the underlying call, which cannot know it was part of
        an escalation.
        """
        self.escalations[_key(purpose, tier, model)] += 1

    def record_model_switch(self) -> None:
        self.model_switches += 1

    @staticmethod
    def _histogram(store: dict[str, Histogram], key: str) -> Histogram:
        if key not in store:
            store[key] = Histogram()
        return store[key]

    # -- reading -----------------------------------------------------------

    @property
    def total_requests(self) -> int:
        return sum(self.requests.values())

    @property
    def total_failures(self) -> int:
        return sum(self.failures.values())

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens_in.values()) + sum(self.tokens_out.values())

    def schema_failure_rate(self) -> float:
        """The metric with a 2 % alert threshold in docs/14 §6.

        A rising rate is the earliest signal that a model or a prompt template has
        degraded, which is why it is tracked separately from ordinary failures.
        """
        total = self.total_requests + sum(self.schema_failures.values())
        return sum(self.schema_failures.values()) / total if total else 0.0

    def snapshot(self) -> Mapping[str, Any]:
        return {
            "requests": self.total_requests,
            "failures": self.total_failures,
            "retries": sum(self.retries.values()),
            "schema_failures": sum(self.schema_failures.values()),
            "schema_failure_rate": round(self.schema_failure_rate(), 4),
            "escalations": sum(self.escalations.values()),
            "model_switches": self.model_switches,
            "tokens_in": sum(self.tokens_in.values()),
            "tokens_out": sum(self.tokens_out.values()),
            "by_purpose": {
                key: {
                    "requests": count,
                    "tokens_out": self.tokens_out.get(key, 0),
                    **self.duration.get(key, Histogram()).snapshot(),
                }
                for key, count in sorted(self.requests.items())
            },
        }

    def prometheus(self) -> str:
        """Prometheus text format, for `/v1/metrics` (docs/19 §6)."""
        lines: list[str] = []

        def emit(name: str, kind: str, help_text: str, samples: Sequence[tuple[str, Any]]) -> None:
            if not samples:
                return
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            lines.extend(f"{name}{labels} {value}" for labels, value in samples)

        emit(
            "hedwig_llm_requests_total",
            "counter",
            "Completed model calls.",
            [(_labels(key), value) for key, value in sorted(self.requests.items())],
        )
        emit(
            "hedwig_llm_failures_total",
            "counter",
            "Failed model calls by error code.",
            [
                (_labels(key.split("|")[0], code=key.split("|")[1]), value)
                for key, value in sorted(self.failures.items())
            ],
        )
        emit(
            "hedwig_llm_retries_total",
            "counter",
            "Retried attempts.",
            [(_labels(key), value) for key, value in sorted(self.retries.items())],
        )
        emit(
            "hedwig_llm_schema_failures_total",
            "counter",
            "Structured outputs that could not be validated.",
            [(_labels(key), value) for key, value in sorted(self.schema_failures.items())],
        )
        emit(
            "hedwig_llm_tokens_total",
            "counter",
            "Tokens consumed and produced.",
            [
                *[
                    (_labels(key, direction="in"), value)
                    for key, value in sorted(self.tokens_in.items())
                ],
                *[
                    (_labels(key, direction="out"), value)
                    for key, value in sorted(self.tokens_out.items())
                ],
            ],
        )

        for name, store, help_text in (
            ("hedwig_llm_call_duration_seconds", self.duration, "Model call duration."),
            ("hedwig_first_token_seconds", self.first_token, "Time to first streamed token."),
        ):
            if not store:
                continue
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} histogram")
            for key, histogram in sorted(store.items()):
                cumulative = 0
                for index, edge in enumerate(histogram.buckets):
                    cumulative += histogram.counts[index]
                    lines.append(f"{name}_bucket{_labels(key, le=str(edge))} {cumulative}")
                lines.append(f"{name}_bucket{_labels(key, le='+Inf')} {histogram.count}")
                lines.append(f"{name}_sum{_labels(key)} {histogram.total:.6f}")
                lines.append(f"{name}_count{_labels(key)} {histogram.count}")

        lines.append("# HELP hedwig_llm_model_switches_total Runtime model changes.")
        lines.append("# TYPE hedwig_llm_model_switches_total counter")
        lines.append(f"hedwig_llm_model_switches_total {self.model_switches}")
        return "\n".join(lines) + "\n"


def _key(purpose: str, tier: str, model: str) -> str:
    return f"{purpose}|{tier}|{model}"


def _labels(key: str, **extra: str) -> str:
    purpose, tier, model = key.split("|")
    pairs = {"purpose": purpose, "tier": tier, "model": model, **extra}
    rendered = ",".join(f'{name}="{_escape(value)}"' for name, value in pairs.items())
    return "{" + rendered + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
