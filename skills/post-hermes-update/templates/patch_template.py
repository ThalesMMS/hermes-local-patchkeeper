"""Template patch module for patchkeeper.py.

Copy this file into scripts/patches/, rename it, set PATCH_ID, and enable it
only after replacing every placeholder. Keep all checks secret-safe.
"""
from __future__ import annotations

from pathlib import Path


PATCH_ID = "replace-me"
DESCRIPTION = "Describe the local Hermes customization in public-safe terms."
TARGET_FILES = ("path/inside/hermes-agent.py",)
RECOMMENDED_TESTS = ("python -m pytest tests/path/test_example.py -q",)
ENABLED = False


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def is_present(repo: Path) -> bool:
    """Return true when upstream or a prior apply already has the behavior."""
    target = repo / TARGET_FILES[0]
    if not target.exists():
        return False
    text = _read(target)
    return "REPLACE_WITH_PUBLIC_SAFE_PRESENT_MARKER" in text


def check_compatible(repo: Path) -> tuple[bool, list[str]]:
    """Check exact anchors without writing files."""
    notes: list[str] = []
    target = repo / TARGET_FILES[0]
    if not target.exists():
        return False, [f"missing target file: {TARGET_FILES[0]}"]

    text = _read(target)
    anchor = "REPLACE_WITH_EXACT_ANCHOR"
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        notes.append(f"required anchor must appear exactly once; found {anchor_count}")

    return not notes, notes


def apply(repo: Path) -> dict[str, object]:
    """Apply the patch idempotently after compatibility has passed."""
    target = repo / TARGET_FILES[0]
    if is_present(repo):
        return {"changed_files": [], "notes": ["already present"]}

    compatible, notes = check_compatible(repo)
    if not compatible:
        raise RuntimeError("; ".join(notes))

    text = _read(target)
    anchor = "REPLACE_WITH_EXACT_ANCHOR"
    updated = text.replace(
        anchor,
        f"{anchor}\nREPLACE_WITH_PUBLIC_SAFE_PATCH_CONTENT",
        1,
    )

    if updated == text:
        return {"changed_files": [], "notes": ["no changes made"]}

    _write(target, updated)
    return {"changed_files": [TARGET_FILES[0]], "notes": ["patch applied"]}
