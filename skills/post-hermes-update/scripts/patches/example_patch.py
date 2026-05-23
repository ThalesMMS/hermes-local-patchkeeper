"""Disabled example patch module for patchkeeper.py.

This example intentionally does not target any real Hermes bug. It demonstrates
the module interface with a harmless marker file.
"""
from __future__ import annotations

from pathlib import Path


PATCH_ID = "example-marker-file"
DESCRIPTION = "Create a harmless marker file showing how an idempotent patch works."
TARGET_FILES = ("docs/local_patchkeeper_example.txt",)
RECOMMENDED_TESTS = ("git diff -- docs/local_patchkeeper_example.txt",)
ENABLED = False

MARKER = "local patchkeeper example marker\n"


def is_present(repo: Path) -> bool:
    target = repo / TARGET_FILES[0]
    return target.exists() and target.read_text(encoding="utf-8") == MARKER


def check_compatible(repo: Path) -> tuple[bool, list[str]]:
    if not repo.exists() or not repo.is_dir():
        return False, ["repo path is not an existing directory"]
    docs_dir = repo / "docs"
    if docs_dir.exists() and not docs_dir.is_dir():
        return False, ["docs exists but is not a directory"]
    return True, ["example patch is compatible"]


def apply(repo: Path) -> dict[str, object]:
    docs_dir = repo / "docs"
    docs_dir.mkdir(exist_ok=True)
    target = repo / TARGET_FILES[0]
    if is_present(repo):
        return {"changed_files": [], "notes": ["marker already present"]}
    target.write_text(MARKER, encoding="utf-8")
    return {"changed_files": [TARGET_FILES[0]], "notes": ["created example marker file"]}
