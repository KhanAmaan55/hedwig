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
