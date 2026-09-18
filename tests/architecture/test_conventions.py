"""Tests that enforce the documentation.

Architecture rules that rely on discipline decay. These make the rules in `docs/` fail the
build instead (docs/20 §8).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "hedwig"

SOURCE_FILES = sorted(SOURCE_ROOT.rglob("*.py"))


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT / "src").with_suffix("")
    parts = [part for part in relative.parts if part != "__init__"]
    return ".".join(parts)


# --- the clock seam ---------------------------------------------------------------

WALL_CLOCK_CALLS = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("time", "time"),
}

WALL_CLOCK_ALLOWED = {
    "hedwig.core.clock",
    # The ULID factory needs epoch milliseconds and takes an injectable `now_ms`; the
    # module-level default is the one sanctioned fallback (docs/05 §3.1).
    "hedwig.core.ids",
}


def test_nothing_reads_the_wall_clock_outside_the_clock_module() -> None:
    """docs/03 §5.6 — this is what makes year-long tests possible in milliseconds."""
    offenders: list[str] = []

    for path in SOURCE_FILES:
        module = _module_name(path)
        if module in WALL_CLOCK_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and (owner.id, node.func.attr) in WALL_CLOCK_CALLS:
                offenders.append(f"{module}:{node.lineno} {owner.id}.{node.func.attr}()")

    assert not offenders, (
        "These modules read the wall clock directly. Inject the `Clock` port instead:\n  "
        + "\n  ".join(offenders)
    )


# --- ports stay pure --------------------------------------------------------------


def test_port_value_objects_are_frozen() -> None:
    """docs/03 §2 rule 4 — no shared mutable state across a module boundary."""
    offenders: list[str] = []

    for path in sorted((SOURCE_ROOT / "core" / "ports").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for decorator in node.decorator_list:
                if not _is_dataclass(decorator):
                    continue
                if not _has_keyword(decorator, "frozen"):
                    offenders.append(f"{_module_name(path)}.{node.name}")

    assert not offenders, "Value objects crossing a port must be frozen: " + ", ".join(offenders)


def test_ports_contain_no_implementation() -> None:
    """A port module defines protocols and value objects. Nothing that runs."""
    offenders: list[str] = []

    for path in sorted((SOURCE_ROOT / "core" / "ports").rglob("*.py")):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                is_protocol = any(
                    isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
                )
                is_dataclass = any(_is_dataclass(d) for d in node.decorator_list)
                is_enum = any(
                    isinstance(base, ast.Name) and base.id.endswith("Enum") for base in node.bases
                )
                if not (is_protocol or is_dataclass or is_enum):
                    offenders.append(f"{module}.{node.name}")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                offenders.append(f"{module}.{node.name}()")

    assert not offenders, "Ports must not contain implementations: " + ", ".join(offenders)


def _is_dataclass(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Name) and target.id == "dataclass"


def _has_keyword(decorator: ast.expr, name: str) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    return any(
        keyword.arg == name and isinstance(keyword.value, ast.Constant) and keyword.value.value
        for keyword in decorator.keywords
    )


# --- layering ---------------------------------------------------------------------


def test_import_linter_contracts_hold() -> None:
    """docs/02 §4.1 — layers, core independence, port purity."""
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint-imports", "--no-cache"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_source_file_belongs_to_a_documented_layer() -> None:
    """A new top-level package must be placed in a layer, not left floating."""
    known = {
        "core",
        "llm",
        "sessions",
        "emotion",
        "memory",
        "brain",
        "api",
        "cli",
        "wiring",
        "__init__",
        "__main__",
    }
    top_level = {
        path.relative_to(SOURCE_ROOT).parts[0].removesuffix(".py") for path in SOURCE_FILES
    }
    unplaced = top_level - known
    assert not unplaced, (
        f"New packages {sorted(unplaced)} must be added to .importlinter and docs/02 §5.1"
    )


# --- emotion: INV-3, checked against the document --------------------------------


def _binding_table() -> list[tuple[str, str]]:
    """Rows of the behaviour-binding table in docs/09 §6, as (dimension, binding)."""
    text = (REPO_ROOT / "docs" / "09-emotion-engine.md").read_text(encoding="utf-8")
    section = text.split("## 6. Behaviour bindings", 1)[1].split("### 6.1", 1)[0]

    rows: list[tuple[str, str]] = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append((cells[0].strip("`"), cells[1]))
    return rows


def test_every_emotional_dimension_has_a_documented_binding() -> None:
    """INV-3, half of it: a dimension nothing reads is decoration (docs/09 §1).

    Decoration is worse than nothing here, because it invites the user to believe something
    false about what is happening inside.
    """
    from hedwig.core.ports.emotion import DIMENSIONS

    bound = {dimension for dimension, _ in _binding_table()}
    missing = {d.value for d in DIMENSIONS} - bound

    assert not missing, f"{sorted(missing)} appear in the state vector but bind to no behaviour"


def test_the_binding_table_names_no_dimension_that_does_not_exist() -> None:
    """The other half: a documented binding for a dimension we removed is a lie the code
    cannot contradict."""
    from hedwig.core.ports.emotion import DIMENSIONS

    known = {d.value for d in DIMENSIONS}
    documented = {dimension for dimension, _ in _binding_table()}

    assert documented <= known, (
        f"docs/09 §6 binds dimensions that do not exist: {documented - known}"
    )


def test_every_dimension_actually_changes_the_behaviour_parameters() -> None:
    """The document says it; this asserts the code does it."""
    from hedwig.core.ports.emotion import DIMENSIONS, EmotionState
    from hedwig.emotion.bindings import behaviour

    for dimension in DIMENSIONS:
        low = behaviour(EmotionState().with_dimension(dimension, 0.0))
        high = behaviour(EmotionState().with_dimension(dimension, 1.0))
        assert low != high, f"{dimension.value} is documented as bound but changes nothing"


def test_the_emotion_module_contains_no_model_call() -> None:
    """docs/09 §4.2: rules only, no model, ever.

    A grep rather than an abstraction, because the thing being prevented is someone adding
    an import in a hurry — and this fails the build when they do.
    """
    emotion_sources = sorted((SOURCE_ROOT / "emotion").rglob("*.py"))
    assert emotion_sources

    for path in emotion_sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("hedwig.llm"), (
                    f"{path.name} imports the language model gateway; docs/09 §4.2 forbids it"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("hedwig.llm"), (
                        f"{path.name} imports the language model gateway; docs/09 §4.2 forbids it"
                    )
