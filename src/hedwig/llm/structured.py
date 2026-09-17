"""Structured output: parse, repair, validate (docs/14 §6).

The cognitive layer depends on reliable JSON from a small model. Treating that casually is
how the whole thing becomes flaky, so the pipeline is explicit:

    parse → repair → validate → retry (temperature 0) → escalate a tier → give up

`repair` exists because the common small-model failure is not invalid JSON, it is *valid
JSON wrapped in prose* — "Here is the result: {...} Hope that helps!". Extracting the
largest balanced object costs nothing and rescues most of them.

Validation uses `jsonschema` rather than a hand-rolled subset. Reimplementing JSON Schema is
a classic way to be subtly wrong for years.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError

MAX_REPAIR_SCAN = 200_000
"""Guard against pathological input; real model output is orders of magnitude smaller."""


def parse_json(text: str) -> Any | None:
    """Parse, repairing the usual small-model wrapping. `None` if nothing usable is there."""
    stripped = text.strip()
    if not stripped:
        return None

    for candidate in _candidates(stripped):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


def _candidates(text: str) -> list[str]:
    """Progressively more forgiving readings of the text, best first."""
    candidates = [text]

    # ```json fenced blocks — extremely common, and trivially recoverable.
    if "```" in text:
        fenced = text.split("```")
        for block in fenced[1:]:
            body = block.split("\n", 1)
            if len(body) == 2:
                candidates.append(body[1].rsplit("```", 1)[0].strip())

    extracted = _largest_balanced(text)
    if extracted is not None:
        candidates.append(extracted)

    return candidates


def _largest_balanced(text: str) -> str | None:
    """The outermost balanced `{...}` or `[...]`, ignoring braces inside strings."""
    if len(text) > MAX_REPAIR_SCAN:
        text = text[:MAX_REPAIR_SCAN]

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None


def validate(value: Any, schema: Mapping[str, Any]) -> list[str]:
    """Return human-readable validation errors. Empty means valid.

    Errors are returned rather than raised because they are fed back to the model on the
    retry — a model told *which* field it got wrong usually fixes it.
    """
    validator = Draft202012Validator(dict(schema))
    problems: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda e: list(e.path)):
        problems.append(_describe(error))
    return problems


def _describe(error: JsonSchemaValidationError) -> str:
    path = ".".join(str(part) for part in error.path)
    return f"{path or '<root>'}: {error.message}"


def repair_instruction(errors: list[str], schema: Mapping[str, Any]) -> str:
    """The correction message appended on a retry.

    Short and specific. A long re-explanation of the schema makes small models worse, not
    better; naming the broken fields makes them better.
    """
    listed = "\n".join(f"- {problem}" for problem in errors[:8])
    return (
        "Your previous reply did not match the required JSON schema.\n"
        f"Problems:\n{listed}\n\n"
        "Reply with JSON only — no prose, no code fences — matching this schema:\n"
        f"{json.dumps(dict(schema), separators=(',', ':'))}"
    )
