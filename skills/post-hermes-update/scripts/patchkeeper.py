#!/usr/bin/env python3
"""Audit and apply user-owned Hermes local patch modules.

Default mode is read-only. Use --apply only after explicit human approval.
Patch modules are trusted executable Python code. This runner reduces accidental
footguns, but it is not a sandbox: do not run it against untrusted patch dirs.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any


sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PATCH_DIR = SCRIPT_DIR / "patches"
EXIT_FAILURE_CONDITION = 1
EXIT_USAGE = 2
EXIT_READ_ONLY_VIOLATION = 3
EXIT_APPLY_FAILED = 4
SNAPSHOT_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
SNAPSHOT_EXCLUDED_FILE_NAMES = frozenset({".coverage"})
SNAPSHOT_EXCLUDED_SUFFIXES = frozenset({".pyc"})


@dataclass
class PatchModule:
    patch_id: str
    description: str
    target_files: tuple[str, ...]
    recommended_tests: tuple[str, ...]
    enabled: bool
    path: Path
    module: ModuleType | None = None
    load_error: str | None = None


@dataclass
class PatchReport:
    patch_id: str
    description: str
    enabled: bool
    status: str = "unknown"
    target_files: tuple[str, ...] = ()
    present: bool = False
    compatible: bool = False
    applied: bool = False
    changed_files: tuple[str, ...] = ()
    actual_changed_files: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    recommended_tests: tuple[str, ...] = ()
    module_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "description": self.description,
            "enabled": self.enabled,
            "status": self.status,
            "target_files": list(self.target_files),
            "present": self.present,
            "compatible": self.compatible,
            "applied": self.applied,
            "changed_files": list(self.changed_files),
            "actual_changed_files": list(self.actual_changed_files),
            "notes": self.notes,
            "recommended_tests": list(self.recommended_tests),
            "module_path": self.module_path,
        }


class PatchkeeperError(Exception):
    """Raised for expected CLI errors."""


def resolve_repo(repo_arg: str | None) -> Path:
    if repo_arg:
        return Path(repo_arg).expanduser().resolve()

    for env_name in ("HERMES_AGENT_REPO", "HERMES_REPO"):
        env_value = os.getenv(env_name)
        if env_value:
            return Path(env_value).expanduser().resolve()

    hermes_home = os.getenv("HERMES_HOME")
    if hermes_home:
        return (Path(hermes_home).expanduser() / "hermes-agent").resolve()

    return (Path.home() / ".hermes" / "hermes-agent").resolve()


def run_git(repo: Path, args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=5,
        ).strip()
    except Exception as exc:
        return f"[git {' '.join(args)} failed: {exc}]"


def is_git_repo(repo: Path) -> bool:
    return run_git(repo, ["rev-parse", "--is-inside-work-tree"]) == "true"


def git_summary(repo: Path) -> dict[str, object]:
    if not is_git_repo(repo):
        return {
            "is_git_repo": "false",
            "dirty_working_tree": False,
            "untracked_files": False,
        }

    status_short = run_git(repo, ["status", "--short"])
    status_lines = [line for line in status_short.splitlines() if line]
    return {
        "is_git_repo": "true",
        "branch": run_git(repo, ["branch", "--show-current"]),
        "status_short": status_short,
        "status_branch": run_git(repo, ["status", "-sb"]),
        "dirty_working_tree": bool(status_lines),
        "untracked_files": any(line.startswith("?? ") for line in status_lines),
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_symlink(path: Path) -> str:
    try:
        return f"<SYMLINK>{os.readlink(path)}"
    except OSError as exc:
        return f"<SYMLINK_READ_ERROR>{exc}"


def _is_snapshot_excluded(path: Path, is_dir: bool, full: bool) -> bool:
    if full:
        return False
    if is_dir:
        return path.name in SNAPSHOT_EXCLUDED_DIR_NAMES
    return path.name in SNAPSHOT_EXCLUDED_FILE_NAMES or path.suffix in SNAPSHOT_EXCLUDED_SUFFIXES


def snapshot_repo(repo: Path, *, full: bool = False) -> dict[str, str]:
    """Return a lightweight content snapshot for --check read-only enforcement."""
    snapshot: dict[str, str] = {}
    for root, dirnames, filenames in os.walk(repo, topdown=True, followlinks=False):
        root_path = Path(root)
        dirnames[:] = sorted(dirnames)
        filenames.sort()

        for dirname in list(dirnames):
            path = root_path / dirname
            try:
                rel = path.relative_to(repo).as_posix()
            except ValueError:
                dirnames.remove(dirname)
                continue
            if rel == ".git":
                dirnames.remove(dirname)
                continue
            if path.is_symlink():
                snapshot[rel] = _snapshot_symlink(path)
                dirnames.remove(dirname)
                continue
            if _is_snapshot_excluded(path, is_dir=True, full=full):
                dirnames.remove(dirname)
                continue
            snapshot[f"{rel}/"] = "<DIR>"

        for filename in filenames:
            path = root_path / filename
            try:
                rel = path.relative_to(repo).as_posix()
            except ValueError:
                continue
            if rel == ".git":
                continue
            if path.is_symlink():
                snapshot[rel] = _snapshot_symlink(path)
            elif _is_snapshot_excluded(path, is_dir=False, full=full):
                continue
            elif path.is_file():
                snapshot[rel] = _hash_file(path)
            else:
                snapshot[rel] = "<SPECIAL>"
    return snapshot


def diff_snapshots(before: dict[str, str], after: dict[str, str], *, include_dirs: bool = True) -> list[str]:
    changes: list[str] = []
    keys = sorted(set(before) | set(after))
    for key in keys:
        if not include_dirs and key.endswith("/"):
            continue
        if key not in before or key not in after or before[key] != after[key]:
            changes.append(key)
    return changes


def literal_patch_id(path: Path) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "PATCH_ID" in names and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "PATCH_ID" and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return None


def selected_patch_paths(patch_dir: Path, only: list[str] | None) -> list[Path]:
    if not patch_dir.exists():
        return []
    all_paths = [path for path in sorted(patch_dir.glob("*.py")) if path.name != "__init__.py"]
    if not only:
        return all_paths

    wanted = set(only)
    selected: list[Path] = []
    matched: set[str] = set()
    for path in all_paths:
        patch_id = literal_patch_id(path)
        identifiers = {path.stem}
        if patch_id:
            identifiers.add(patch_id)
        hits = identifiers & wanted
        if hits:
            selected.append(path)
            matched.update(hits)
            if patch_id in wanted:
                matched.add(patch_id)
            if path.stem in wanted:
                matched.add(path.stem)

    missing = sorted(wanted - matched)
    if missing:
        raise PatchkeeperError(
            "unknown patch id(s): "
            + ", ".join(missing)
            + "; --only matches literal PATCH_ID values or module filenames without .py"
        )
    return selected


def load_module(path: Path) -> PatchModule:
    spec = importlib.util.spec_from_file_location(f"patchkeeper_patch_{path.stem}", path)
    if spec is None or spec.loader is None:
        return PatchModule(path.stem, path.stem, (), (), False, path, None, "could not create module spec")

    module = importlib.util.module_from_spec(spec)
    try:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            spec.loader.exec_module(module)
        load_notes = stream.getvalue()
    except Exception as exc:
        return PatchModule(path.stem, path.stem, (), (), False, path, None, f"failed to load module: {exc}")

    patch_id = str(getattr(module, "PATCH_ID", path.stem))
    description = str(getattr(module, "DESCRIPTION", "No description provided."))
    target_files = tuple(str(item) for item in getattr(module, "TARGET_FILES", ()))
    recommended_tests = tuple(str(item) for item in getattr(module, "RECOMMENDED_TESTS", ()))
    enabled = bool(getattr(module, "ENABLED", True))
    patch = PatchModule(patch_id, description, target_files, recommended_tests, enabled, path, module)
    if load_notes:
        patch.load_error = f"module emitted output during import; output suppressed ({len(load_notes)} chars)"
    return patch


def discover_patches(patch_dir: Path, only: list[str] | None = None) -> list[PatchModule]:
    patches = [load_module(path) for path in selected_patch_paths(patch_dir, only)]

    seen: set[str] = set()
    duplicates: set[str] = set()
    for patch in patches:
        if patch.patch_id in seen:
            duplicates.add(patch.patch_id)
        seen.add(patch.patch_id)
    if duplicates:
        names = ", ".join(sorted(duplicates))
        raise PatchkeeperError(f"duplicate PATCH_ID values: {names}")

    return patches


def call_safely(func: Any, *args: Any) -> tuple[Any, list[str]]:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        value = func(*args)
    notes: list[str] = []
    if stream.getvalue():
        notes.append(f"{func.__name__} emitted output; output suppressed")
    return value, notes


def base_report(patch: PatchModule) -> PatchReport:
    return PatchReport(
        patch_id=patch.patch_id,
        description=patch.description,
        enabled=patch.enabled,
        target_files=patch.target_files,
        recommended_tests=patch.recommended_tests,
        module_path=str(patch.path),
    )


def validate_repo_relative_path(repo: Path, path_value: str, label: str) -> str | None:
    raw = str(path_value)
    if not raw:
        return f"invalid {label} path: empty path"
    candidate = Path(raw)
    if candidate.is_absolute():
        return f"invalid {label} path: absolute paths are not allowed: {raw}"
    if raw.startswith("~"):
        return f"invalid {label} path: home-relative paths are not allowed: {raw}"
    if any(part == ".." for part in candidate.parts):
        return f"invalid {label} path: parent traversal is not allowed: {raw}"
    repo_root = repo.resolve()
    resolved = (repo_root / candidate).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return f"invalid {label} path: path escapes repo: {raw}"
    return None


def validate_patch_paths(repo: Path, patch: PatchModule, report: PatchReport) -> bool:
    errors = [
        error
        for path in patch.target_files
        if (error := validate_repo_relative_path(repo, path, "target")) is not None
    ]
    if errors:
        report.notes.extend(errors)
        report.status = "invalid_target_paths"
        report.present = False
        report.compatible = False
        return False
    return True


def validate_changed_files(repo: Path, changed_files: tuple[str, ...]) -> list[str]:
    return [
        error
        for path in changed_files
        if (error := validate_repo_relative_path(repo, path, "changed_file")) is not None
    ]


def format_path_list(paths: list[str], limit: int = 20) -> str:
    if len(paths) <= limit:
        return ", ".join(paths)
    shown = ", ".join(paths[:limit])
    return f"{shown}, ... ({len(paths) - limit} more)"


def record_actual_apply_changes(
    report: PatchReport,
    before_snapshot: dict[str, str],
    after_snapshot: dict[str, str],
    changed_files: tuple[str, ...],
) -> bool:
    actual_changes = tuple(diff_snapshots(before_snapshot, after_snapshot, include_dirs=False))
    report.actual_changed_files = actual_changes
    actual_set = set(actual_changes)
    reported_set = set(changed_files)
    unexpected = sorted(actual_set - reported_set)
    missing_report = sorted(reported_set - actual_set)

    if unexpected:
        report.applied = False
        report.compatible = False
        report.status = "apply_unreported_changes"
        report.notes.append(f"apply changed unreported file(s): {format_path_list(unexpected)}")
        if missing_report:
            report.notes.append(f"apply reported unchanged file(s): {format_path_list(missing_report)}")
        return False

    if missing_report:
        report.applied = False
        report.compatible = False
        report.status = "apply_misreported_changes"
        report.notes.append(f"apply reported unchanged file(s): {format_path_list(missing_report)}")
        return False

    return True


def require_interface(repo: Path, patch: PatchModule, report: PatchReport) -> bool:
    if patch.module is None:
        report.notes.append(patch.load_error or "module did not load")
        report.status = "load_error"
        return False

    if patch.load_error:
        report.notes.append(patch.load_error)

    if not validate_patch_paths(repo, patch, report):
        return False

    missing = [
        name
        for name in ("is_present", "check_compatible", "apply")
        if not callable(getattr(patch.module, name, None))
    ]
    if missing:
        report.notes.append(f"missing required callables: {', '.join(missing)}")
        report.status = "load_error"
        return False
    return True


def normalize_compat(value: Any) -> tuple[bool, list[str]]:
    if isinstance(value, tuple) and len(value) == 2:
        compatible = bool(value[0])
        notes_value = value[1]
        if notes_value is None:
            notes: list[str] = []
        elif isinstance(notes_value, (list, tuple)):
            notes = [str(item) for item in notes_value]
        else:
            notes = [str(notes_value)]
        return compatible, notes
    return False, ["check_compatible must return tuple[bool, list[str]]"]


def normalize_apply_result(value: Any) -> tuple[tuple[str, ...], list[str]]:
    if value is None:
        return (), []
    if not isinstance(value, dict):
        return (), ["apply must return a dict with changed_files and optional notes"]

    changed_value = value.get("changed_files", ())
    notes_value = value.get("notes", ())
    if isinstance(changed_value, str):
        changed_files = (changed_value,)
    else:
        changed_files = tuple(str(item) for item in changed_value or ())

    if isinstance(notes_value, str):
        notes = [notes_value]
    else:
        notes = [str(item) for item in notes_value or ()]

    return changed_files, notes


def audit_patch(repo: Path, patch: PatchModule) -> PatchReport:
    report = base_report(patch)
    if not patch.enabled:
        report.status = "disabled"
        report.notes.append("patch module is disabled; set ENABLED = True after customizing it")
        return report
    if not require_interface(repo, patch, report):
        return report

    assert patch.module is not None
    try:
        present, notes = call_safely(patch.module.is_present, repo)
        report.notes.extend(notes)
        report.present = bool(present)
        if report.present:
            report.compatible = True
            report.status = "present"
            report.notes.append("functionality already present; patch is not needed")
            return report

        compat_value, notes = call_safely(patch.module.check_compatible, repo)
        report.notes.extend(notes)
        compatible, compat_notes = normalize_compat(compat_value)
        report.compatible = compatible
        report.notes.extend(compat_notes)
        report.status = "missing_compatible" if compatible else "missing_incompatible"
    except Exception as exc:
        report.present = False
        report.compatible = False
        report.status = "audit_failed"
        report.notes.append(f"audit failed: {exc}")

    return report


def apply_patch(repo: Path, patch: PatchModule) -> PatchReport:
    before_snapshot = snapshot_repo(repo, full=True)
    report = audit_patch(repo, patch)
    audit_snapshot = snapshot_repo(repo, full=True)
    audit_changes = diff_snapshots(before_snapshot, audit_snapshot, include_dirs=False)
    if audit_changes:
        report.actual_changed_files = tuple(audit_changes)
        report.applied = False
        report.compatible = False
        report.status = "apply_unreported_changes"
        report.notes.append(f"audit changed file(s) before apply: {format_path_list(audit_changes)}")
        return report
    before_snapshot = audit_snapshot
    if not patch.enabled:
        record_actual_apply_changes(report, before_snapshot, snapshot_repo(repo, full=True), ())
        return report
    if report.present:
        report.applied = False
        report.status = "present"
        report.notes.append("skipped apply because patch is already present")
        record_actual_apply_changes(report, before_snapshot, snapshot_repo(repo, full=True), ())
        return report
    if not report.compatible:
        report.applied = False
        if report.status == "unknown":
            report.status = "missing_incompatible"
        report.notes.append("skipped apply because patch is incompatible")
        record_actual_apply_changes(report, before_snapshot, snapshot_repo(repo, full=True), ())
        return report
    if patch.module is None:
        report.applied = False
        report.status = "load_error"
        record_actual_apply_changes(report, before_snapshot, snapshot_repo(repo, full=True), ())
        return report

    try:
        result, notes = call_safely(patch.module.apply, repo)
        report.notes.extend(notes)
        changed_files, apply_notes = normalize_apply_result(result)
        report.changed_files = changed_files
        report.notes.extend(apply_notes)
        invalid_changed = validate_changed_files(repo, changed_files)
        if invalid_changed:
            report.actual_changed_files = tuple(
                diff_snapshots(before_snapshot, snapshot_repo(repo, full=True), include_dirs=False)
            )
            report.applied = False
            report.compatible = False
            report.status = "invalid_changed_files"
            report.notes.extend(invalid_changed)
            return report

        present_after, notes = call_safely(patch.module.is_present, repo)
        report.notes.extend(notes)
        report.present = bool(present_after)
        after_snapshot = snapshot_repo(repo, full=True)
        if not record_actual_apply_changes(report, before_snapshot, after_snapshot, changed_files):
            return report

        report.applied = bool(report.actual_changed_files)
        if report.present:
            report.status = "applied" if report.applied else "present"
        else:
            report.status = "apply_incomplete"
            report.notes.append("apply completed but is_present still returned false")
    except Exception as exc:
        report.actual_changed_files = tuple(
            diff_snapshots(before_snapshot, snapshot_repo(repo, full=True), include_dirs=False)
        )
        report.compatible = False
        report.applied = False
        report.status = "apply_failed"
        report.notes.append(f"apply failed: {exc}")

    return report


def summarize_reports(reports: list[PatchReport]) -> dict[str, bool]:
    return {
        "has_needed_patches": any(report.status == "missing_compatible" for report in reports),
        "has_incompatible_patches": any(
            report.status in {"missing_incompatible", "invalid_target_paths", "audit_failed"}
            for report in reports
        ),
        "has_load_errors": any(report.status == "load_error" for report in reports),
        "has_apply_failures": any(
            report.status
            in {
                "apply_failed",
                "apply_incomplete",
                "apply_misreported_changes",
                "apply_unreported_changes",
                "invalid_changed_files",
            }
            for report in reports
        ),
    }


def render(
    repo: Path,
    patch_dir: Path,
    mode: str,
    reports: list[PatchReport],
    repo_changes_during_check: list[str] | None = None,
    git_info: dict[str, object] | None = None,
    preflight_error: str | None = None,
) -> str:
    summary = summarize_reports(reports)
    changes = repo_changes_during_check or []
    git_payload = git_info if git_info is not None else git_summary(repo)
    payload = {
        "mode": mode,
        "repo": str(repo),
        "patch_dir": str(patch_dir),
        "git": git_payload,
        "dirty_working_tree": bool(git_payload.get("dirty_working_tree", False)),
        "untracked_files": bool(git_payload.get("untracked_files", False)),
        "read_only_violation": bool(changes),
        "repo_changes_during_check": changes,
        **summary,
        "results": [report.as_dict() for report in reports],
    }
    if preflight_error:
        payload["error"] = preflight_error
    return json.dumps(payload, indent=2, sort_keys=True)


def failure_exit_code(args: argparse.Namespace, mode: str, reports: list[PatchReport], read_only_violation: bool) -> int:
    if read_only_violation:
        return EXIT_READ_ONLY_VIOLATION

    summary = summarize_reports(reports)
    if args.fail_on_needed and summary["has_needed_patches"]:
        return EXIT_FAILURE_CONDITION
    if args.fail_on_incompatible and summary["has_incompatible_patches"]:
        return EXIT_FAILURE_CONDITION
    if args.fail_on_load_error and summary["has_load_errors"]:
        return EXIT_FAILURE_CONDITION
    if args.fail_on_apply_failed and summary["has_apply_failures"]:
        return EXIT_FAILURE_CONDITION
    if mode == "apply" and summary["has_apply_failures"]:
        return EXIT_APPLY_FAILED
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Path to the Hermes Agent checkout")
    parser.add_argument("--check", action="store_true", help="Audit only; do not modify files")
    parser.add_argument("--apply", action="store_true", help="Apply compatible absent patches")
    parser.add_argument("--only", action="append", help="Run only the selected PATCH_ID or module filename; repeatable")
    parser.add_argument(
        "--patch-dir",
        default=str(DEFAULT_PATCH_DIR),
        help="Directory containing patch modules (default: scripts/patches)",
    )
    parser.add_argument("--fail-on-needed", action="store_true", help="Exit 1 if check finds a missing compatible patch")
    parser.add_argument("--fail-on-incompatible", action="store_true", help="Exit 1 if check finds incompatible or invalid patches")
    parser.add_argument("--fail-on-load-error", action="store_true", help="Exit 1 if any patch module fails to load")
    parser.add_argument("--fail-on-apply-failed", action="store_true", help="Exit 1 if apply reports a failed/incomplete patch")
    parser.add_argument("--fail-on-dirty", action="store_true", help="Exit 1 before running if the target git working tree is dirty")
    parser.add_argument("--require-clean", action="store_true", help="Require a clean target git working tree before running")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow --apply on a dirty target git working tree")
    parser.add_argument(
        "--full-snapshot",
        action="store_true",
        help="Disable default snapshot exclusions during --check",
    )
    args = parser.parse_args()

    if args.check and args.apply:
        parser.error("use --check or --apply, not both")
    if args.require_clean and args.allow_dirty:
        parser.error("use --require-clean or --allow-dirty, not both")
    mode = "apply" if args.apply else "check"

    repo = resolve_repo(args.repo)
    patch_dir = Path(args.patch_dir).expanduser().resolve()

    if not repo.exists():
        print(json.dumps({"error": f"repo does not exist: {repo}"}), file=sys.stderr)
        return EXIT_USAGE
    if not repo.is_dir():
        print(json.dumps({"error": f"repo is not a directory: {repo}"}), file=sys.stderr)
        return EXIT_USAGE

    initial_git_info = git_summary(repo)
    initial_dirty = bool(initial_git_info.get("dirty_working_tree", False))
    preflight_error: str | None = None
    if args.fail_on_dirty and initial_dirty:
        preflight_error = "target git working tree is dirty"
    elif (args.require_clean or (mode == "apply" and not args.allow_dirty)) and initial_dirty:
        preflight_error = "target git working tree must be clean before running"
    if preflight_error:
        print(render(repo, patch_dir, mode, [], git_info=initial_git_info, preflight_error=preflight_error))
        return EXIT_FAILURE_CONDITION

    before_snapshot = snapshot_repo(repo, full=args.full_snapshot) if mode == "check" else None
    try:
        patches = discover_patches(patch_dir, args.only)
        reports = [
            apply_patch(repo, patch) if mode == "apply" else audit_patch(repo, patch)
            for patch in patches
        ]
    except PatchkeeperError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return EXIT_USAGE

    repo_changes_during_check: list[str] = []
    if before_snapshot is not None:
        repo_changes_during_check = diff_snapshots(before_snapshot, snapshot_repo(repo, full=args.full_snapshot))
        if repo_changes_during_check:
            for report in reports:
                report.notes.append("read-only violation: repo changed during --check")

    print(render(repo, patch_dir, mode, reports, repo_changes_during_check))
    return failure_exit_code(args, mode, reports, bool(repo_changes_during_check))


if __name__ == "__main__":
    raise SystemExit(main())
