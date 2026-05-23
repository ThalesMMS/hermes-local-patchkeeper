from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCHKEEPER = ROOT / "skills" / "post-hermes-update" / "scripts" / "patchkeeper.py"
SPEC = importlib.util.spec_from_file_location("patchkeeper_under_test", PATCHKEEPER)
assert SPEC is not None and SPEC.loader is not None
patchkeeper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = patchkeeper
SPEC.loader.exec_module(patchkeeper)


def run_patchkeeper(repo: Path, patch_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PATCHKEEPER), "--repo", str(repo), "--patch-dir", str(patch_dir), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_patch(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")


def test_only_does_not_import_unselected_patch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "safe_patch.py",
        '''
PATCH_ID = "safe"
DESCRIPTION = "safe"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return True

def check_compatible(repo):
    return True, []

def apply(repo):
    return {"changed_files": []}
''',
    )
    write_patch(
        patches / "dangerous_patch.py",
        f'''
from pathlib import Path
Path({str(repo / "imported_unselected.txt")!r}).write_text("import side effect", encoding="utf-8")
PATCH_ID = "dangerous"
DESCRIPTION = "dangerous"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return True

def check_compatible(repo):
    return True, []

def apply(repo):
    return {{"changed_files": []}}
''',
    )

    result = run_patchkeeper(repo, patches, "--check", "--only", "safe")

    assert result.returncode == 0, result.stderr + result.stdout
    assert not (repo / "imported_unselected.txt").exists()
    payload = json.loads(result.stdout)
    assert [item["patch_id"] for item in payload["results"]] == ["safe"]


def test_check_mode_detects_repo_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "mutating_check.py",
        '''
PATCH_ID = "mutating-check"
DESCRIPTION = "mutates during check"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return False

def check_compatible(repo):
    (repo / "unexpected.txt").write_text("bad", encoding="utf-8")
    return True, []

def apply(repo):
    return {"changed_files": []}
''',
    )

    result = run_patchkeeper(repo, patches, "--check")

    assert result.returncode == 3, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["read_only_violation"] is True
    assert "unexpected.txt" in payload["repo_changes_during_check"]


def test_rejects_target_paths_that_escape_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "escaping.py",
        '''
PATCH_ID = "escaping"
DESCRIPTION = "escaping path"
TARGET_FILES = ("../outside.txt",)
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return False

def check_compatible(repo):
    return True, []

def apply(repo):
    return {"changed_files": ["../outside.txt"]}
''',
    )

    result = run_patchkeeper(repo, patches, "--check")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    report = payload["results"][0]
    assert report["patch_id"] == "escaping"
    assert report["compatible"] is False
    assert report["status"] == "invalid_target_paths"
    assert any("invalid target path" in note for note in report["notes"])


def test_check_mode_does_not_flag_python_bytecode_in_patch_dir_inside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patch_dir = repo / "skills" / "post-hermes-update" / "scripts" / "patches"
    patch_dir.mkdir(parents=True)

    write_patch(
        patch_dir / "present.py",
        '''
PATCH_ID = "present"
DESCRIPTION = "already present"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return True

def check_compatible(repo):
    return True, []

def apply(repo):
    return {"changed_files": []}
''',
    )

    result = run_patchkeeper(repo, patch_dir, "--check")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["read_only_violation"] is False
    assert not any("__pycache__" in item for item in payload["repo_changes_during_check"])


def test_fail_on_needed_returns_nonzero_for_missing_compatible_patch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "needed.py",
        '''
PATCH_ID = "needed"
DESCRIPTION = "needed patch"
TARGET_FILES = ("target.txt",)
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return False

def check_compatible(repo):
    return True, []

def apply(repo):
    (repo / "target.txt").write_text("patched", encoding="utf-8")
    return {"changed_files": ["target.txt"]}
''',
    )

    result = run_patchkeeper(repo, patches, "--check", "--fail-on-needed")

    assert result.returncode == 1, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["has_needed_patches"] is True
    assert payload["results"][0]["status"] == "missing_compatible"


def test_apply_fails_when_patch_changes_unreported_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "unreported.py",
        '''
PATCH_ID = "unreported"
DESCRIPTION = "changes an undeclared file"
TARGET_FILES = ("undeclared.txt",)
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return (repo / "undeclared.txt").exists()

def check_compatible(repo):
    return True, []

def apply(repo):
    (repo / "undeclared.txt").write_text("patched", encoding="utf-8")
    return {"changed_files": ["declared.txt"]}
''',
    )

    result = run_patchkeeper(repo, patches, "--apply")

    assert result.returncode == 4, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    report = payload["results"][0]
    assert payload["has_apply_failures"] is True
    assert report["status"] == "apply_unreported_changes"
    assert report["changed_files"] == ["declared.txt"]
    assert report["actual_changed_files"] == ["undeclared.txt"]
    assert any("apply changed unreported file(s): undeclared.txt" in note for note in report["notes"])
    assert any("apply reported unchanged file(s): declared.txt" in note for note in report["notes"])


def test_apply_rejects_invalid_changed_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "invalid_changed.py",
        '''
PATCH_ID = "invalid-changed"
DESCRIPTION = "returns invalid changed files"
TARGET_FILES = ("target.txt",)
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return (repo / "target.txt").exists()

def check_compatible(repo):
    return True, []

def apply(repo):
    (repo / "target.txt").write_text("patched", encoding="utf-8")
    return {"changed_files": ["../outside.txt"]}
''',
    )

    result = run_patchkeeper(repo, patches, "--apply")

    assert result.returncode == 4, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    report = payload["results"][0]
    assert report["status"] == "invalid_changed_files"
    assert report["actual_changed_files"] == ["target.txt"]
    assert any("invalid changed_file path" in note for note in report["notes"])


def test_apply_incomplete_when_presence_check_stays_false(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "incomplete.py",
        '''
PATCH_ID = "incomplete"
DESCRIPTION = "does not satisfy presence check"
TARGET_FILES = ("target.txt",)
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return False

def check_compatible(repo):
    return True, []

def apply(repo):
    (repo / "target.txt").write_text("patched", encoding="utf-8")
    return {"changed_files": ["target.txt"]}
''',
    )

    result = run_patchkeeper(repo, patches, "--apply")

    assert result.returncode == 4, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    report = payload["results"][0]
    assert report["status"] == "apply_incomplete"
    assert report["actual_changed_files"] == ["target.txt"]
    assert any("is_present still returned false" in note for note in report["notes"])


def test_duplicate_patch_ids_are_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    for name in ("first.py", "second.py"):
        write_patch(
            patches / name,
            '''
PATCH_ID = "duplicate"
DESCRIPTION = "duplicate id"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return True

def check_compatible(repo):
    return True, []

def apply(repo):
    return {"changed_files": []}
''',
        )

    result = run_patchkeeper(repo, patches, "--check")

    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "duplicate PATCH_ID values: duplicate"


def test_missing_required_callable_is_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "missing_apply.py",
        '''
PATCH_ID = "missing-apply"
DESCRIPTION = "missing apply"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return False

def check_compatible(repo):
    return True, []
''',
    )

    result = run_patchkeeper(repo, patches, "--check")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    report = payload["results"][0]
    assert report["status"] == "load_error"
    assert any("missing required callables: apply" in note for note in report["notes"])


def test_only_accepts_module_filename_without_extension(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "filename_patch.py",
        '''
PATCH_ID = "patch-id"
DESCRIPTION = "selected by filename"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return True

def check_compatible(repo):
    return True, []

def apply(repo):
    return {"changed_files": []}
''',
    )

    result = run_patchkeeper(repo, patches, "--check", "--only", "filename_patch")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert [item["patch_id"] for item in payload["results"]] == ["patch-id"]


def test_stdout_and_stderr_from_patch_functions_are_suppressed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()

    write_patch(
        patches / "noisy.py",
        '''
import sys

PATCH_ID = "noisy"
DESCRIPTION = "emits output"
TARGET_FILES = ()
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    print("stdout secret")
    return False

def check_compatible(repo):
    print("stderr secret", file=sys.stderr)
    return True, []

def apply(repo):
    return {"changed_files": []}
''',
    )

    result = run_patchkeeper(repo, patches, "--check")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "stdout secret" not in result.stdout
    assert "stderr secret" not in result.stderr
    payload = json.loads(result.stdout)
    notes = payload["results"][0]["notes"]
    assert "is_present emitted output; output suppressed" in notes
    assert "check_compatible emitted output; output suppressed" in notes


def test_dirty_git_repo_is_reported_and_can_fail_preflight(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "untracked.txt").write_text("dirty", encoding="utf-8")

    result = run_patchkeeper(repo, patches, "--check")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["dirty_working_tree"] is True
    assert payload["untracked_files"] is True
    assert payload["git"]["dirty_working_tree"] is True
    assert payload["git"]["untracked_files"] is True

    fail_result = run_patchkeeper(repo, patches, "--check", "--fail-on-dirty")

    assert fail_result.returncode == 1
    fail_payload = json.loads(fail_result.stdout)
    assert fail_payload["error"] == "target git working tree is dirty"
    assert fail_payload["results"] == []


def test_apply_requires_clean_git_repo_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")

    write_patch(
        patches / "would_apply.py",
        '''
PATCH_ID = "would-apply"
DESCRIPTION = "should not run on dirty tree"
TARGET_FILES = ("applied.txt",)
RECOMMENDED_TESTS = ()
ENABLED = True

def is_present(repo):
    return (repo / "applied.txt").exists()

def check_compatible(repo):
    return True, []

def apply(repo):
    (repo / "applied.txt").write_text("patched", encoding="utf-8")
    return {"changed_files": ["applied.txt"]}
''',
    )

    result = run_patchkeeper(repo, patches, "--apply")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "target git working tree must be clean before running"
    assert payload["dirty_working_tree"] is True
    assert not (repo / "applied.txt").exists()


def test_snapshot_records_symlinks_without_following_targets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("before", encoding="utf-8")
    (repo / "link.txt").symlink_to(outside)

    before = patchkeeper.snapshot_repo(repo, full=True)
    outside.write_text("after", encoding="utf-8")
    after = patchkeeper.snapshot_repo(repo, full=True)

    assert before["link.txt"] == f"<SYMLINK>{outside}"
    assert patchkeeper.diff_snapshots(before, after) == []


def test_default_snapshot_excludes_common_cache_and_build_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    node_modules = repo / "node_modules"
    node_modules.mkdir()
    (node_modules / "dep.js").write_text("dependency", encoding="utf-8")
    build = repo / "build"
    build.mkdir()
    (build / "artifact.txt").write_text("artifact", encoding="utf-8")
    (repo / ".coverage").write_text("coverage", encoding="utf-8")
    (repo / "source.txt").write_text("source", encoding="utf-8")

    default_snapshot = patchkeeper.snapshot_repo(repo)
    full_snapshot = patchkeeper.snapshot_repo(repo, full=True)

    assert "node_modules/" not in default_snapshot
    assert "node_modules/dep.js" not in default_snapshot
    assert "build/" not in default_snapshot
    assert "build/artifact.txt" not in default_snapshot
    assert ".coverage" not in default_snapshot
    assert "source.txt" in default_snapshot
    assert "node_modules/dep.js" in full_snapshot
    assert "build/artifact.txt" in full_snapshot
    assert ".coverage" in full_snapshot
