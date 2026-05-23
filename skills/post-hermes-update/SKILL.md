---
name: post-hermes-update
description: Audit and optionally reapply user-owned Hermes Agent local patch modules after a Hermes update. Use when Hermes was updated, a native checkout changed, a Docker image was rebuilt, or the user asks whether local source/image customizations survived.
version: 2.0.0
author: Hermes local patchkeeper
license: MIT
metadata:
  hermes:
    tags: [hermes, update, local-patches, patchkeeper, source-checkout]
---

# post-hermes-update

Use this skill after a Hermes Agent update or image rebuild to audit local source/image customizations managed as patch modules.

Do not use it for normal persistent user state such as `$HERMES_HOME`, `~/.hermes/config.yaml`, `.env`, auth stores, memories, cron definitions, or user skills.

## Required Workflow

1. Locate the Hermes Agent repo from `--repo`, `$HERMES_AGENT_REPO`, `$HERMES_REPO`, `$HERMES_HOME/hermes-agent`, or `~/.hermes/hermes-agent`.
2. Resolve the installed skill directory instead of assuming the current working directory:

```bash
SKILL_DIR=/path/to/post-hermes-update
PATCHKEEPER="$SKILL_DIR/scripts/patchkeeper.py"
```

3. Run a read-only audit:

```bash
python "$PATCHKEEPER" --repo "$HERMES_HOME/hermes-agent" --check
```

4. Report:
   - patches already present upstream and no longer needed;
   - patches still needed and compatible;
   - patches still needed but incompatible and requiring adaptation;
   - dirty working tree or git status risks.
5. Wait for explicit user authorization before editing files.
6. Apply only authorized compatible patches:

```bash
python "$PATCHKEEPER" --repo "$HERMES_HOME/hermes-agent" --apply
```

7. After applying, report changed files and recommended tests from the JSON output.

## Patch Modules

Patch modules live in `scripts/patches/`. Real users should add their own modules; the public package ships only a disabled example.

Each module exposes:

```python
PATCH_ID = "my-patch-id"
DESCRIPTION = "Short public description"
TARGET_FILES = ("relative/path.py",)
RECOMMENDED_TESTS = ("python -m pytest tests/example_test.py -q",)
ENABLED = True

def is_present(repo: Path) -> bool: ...
def check_compatible(repo: Path) -> tuple[bool, list[str]]: ...
def apply(repo: Path) -> dict[str, object]: ...
```

`--check` must be read-only. `patchkeeper.py` snapshots the repo before and after `--check` and exits with code `3` if a patch mutates the repo during audit. This is a guardrail, not a sandbox: patch modules are trusted executable Python code. The default `--check` snapshot skips common heavy cache/build paths; use `--full-snapshot` when a complete scan is required. `--apply` must be explicit, idempotent, and separate. Patch modules must detect upstream functionality before patching and check exact compatibility anchors before editing. `TARGET_FILES` and reported `changed_files` must be repo-relative paths; absolute paths, `~`, and `..` traversal are rejected. During `--apply`, the runner verifies that actual file changes match reported `changed_files`.

## Common Commands

Audit all patches:

```bash
python "$PATCHKEEPER" --check
```

Audit one patch:

```bash
python "$PATCHKEEPER" --check --only my-patch-id
```

Apply one authorized patch:

```bash
python "$PATCHKEEPER" --apply --only my-patch-id
```

Automation checks:

```bash
python "$PATCHKEEPER" --check --fail-on-needed --fail-on-incompatible --fail-on-load-error
```

Exit codes: `0` success/no requested failure, `1` requested `--fail-on-*` or clean-tree condition, `2` CLI/config error, `3` read-only violation during `--check`, `4` failed/incomplete/misreported `--apply`.

## Safety Notes

- Never apply patches blindly after an update.
- `--apply` requires a clean target git working tree by default; use `--allow-dirty` only when explicitly intended.
- Never apply incompatible patches or patches that are already present.
- Never print secrets, auth files, environment dumps, private logs, or sensitive diffs.
- Native checkout files may be overwritten by `hermes update`.
- Docker image files are replaced by image updates; persistent Docker state belongs in a volume such as `/opt/data`.
- This skill preserves local source/image customizations, not config, secrets, or user data.
