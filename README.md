# hermes-local-patchkeeper

Reusable Hermes Agent skills for preserving intentional local source or image customizations across updates.

This project is not for normal Hermes configuration, user data, secrets, memories, cron definitions, or custom user skills. Those should live in persistent Hermes state such as `$HERMES_HOME`, `~/.hermes`, or a Docker volume.

Primary target: native Hermes installs where local source-checkout files under `$HERMES_HOME/hermes-agent` may be overwritten by `hermes update`. Docker/image-layer customizations are supported as a secondary use case only.

Security model: patch modules are trusted executable Python code, not sandboxed data. Do not run `patchkeeper.py` against patch directories you do not control.

## What This Is For

Hermes updates can replace files that live in the Hermes Agent checkout or in a container image layer:

- Native installs: files under `$HERMES_HOME/hermes-agent` or `~/.hermes/hermes-agent` may be overwritten by `hermes update` or by git operations.
- Docker installs: files baked into the image are ephemeral and are replaced when the image is rebuilt or updated.
- Persistent user state: files in `$HERMES_HOME`, `~/.hermes`, or Docker volumes such as `/opt/data` are the right place for config, state, auth, memories, and skills.

Use this project only for local source/image customizations that are intentionally outside persistent user state and need a repeatable post-update audit.

## Included Skills

### `post-hermes-update`

Audits local patch modules after a Hermes update. It checks whether each customization is already present upstream, whether an absent customization is still compatible with the current checkout, and emits a structured report. It applies only explicitly authorized, compatible, absent patches.

### `post-hermes-files-edit`

Captures newly edited non-persistent Hermes files and decides whether the edit should become a reusable patch module consumed by `post-hermes-update`.

The intended flow is:

1. A user edits a Hermes checkout or image-layer file.
2. `post-hermes-files-edit` classifies the edit.
3. Durable local customizations become patch modules.
4. After future updates, `post-hermes-update` audits and, with explicit authorization, reapplies compatible missing customizations.

## Commands

Set the script path explicitly, especially when this is installed as a skill and
your current directory is not the project root:

```bash
POST_HERMES_UPDATE_DIR=/path/to/post-hermes-update
POST_HERMES_FILES_EDIT_DIR=/path/to/post-hermes-files-edit
PATCHKEEPER="$POST_HERMES_UPDATE_DIR/scripts/patchkeeper.py"
```

Audit only:

```bash
python "$PATCHKEEPER" --repo "$HERMES_HOME/hermes-agent" --check
```

Apply authorized compatible patches:

```bash
python "$PATCHKEEPER" --repo "$HERMES_HOME/hermes-agent" --apply
```

Apply one authorized patch:

```bash
python "$PATCHKEEPER" --repo "$HERMES_HOME/hermes-agent" --apply --only my-patch-id
```

Automation-oriented failure checks:

```bash
python "$PATCHKEEPER" --repo "$HERMES_HOME/hermes-agent" --check --fail-on-needed --fail-on-incompatible --fail-on-load-error
```

Exit codes:

- `0`: completed without requested failure condition.
- `1`: requested `--fail-on-*` or clean-tree condition was met.
- `2`: CLI/configuration error.
- `3`: `--check` caused repo changes and violated read-only mode.
- `4`: `--apply` had failed/incomplete patches.

Dirty git working trees are reported as top-level `dirty_working_tree` and
`untracked_files` booleans. Use `--fail-on-dirty` to fail before running on a
dirty target repo. `--apply` requires a clean target git working tree by default;
pass `--allow-dirty` only when you intentionally want to apply onto existing
local changes.

Create a new patch from the template:

```bash
cp "$POST_HERMES_UPDATE_DIR/templates/patch_template.py" "$POST_HERMES_UPDATE_DIR/scripts/patches/my_patch.py"
cp "$POST_HERMES_FILES_EDIT_DIR/templates/patch_design_note.md" /tmp/my_patch_design_note.md
$EDITOR "$POST_HERMES_UPDATE_DIR/scripts/patches/my_patch.py"
python "$PATCHKEEPER" --repo "$HERMES_HOME/hermes-agent" --check --only my-patch-id
```

## Patch Module Contract

Patch modules live in `skills/post-hermes-update/scripts/patches/` and expose:

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

Rules for every patch:

- `is_present()` detects whether upstream already includes the desired behavior.
- `check_compatible()` checks exact anchors before edits and does not write files.
- `apply()` is explicit, idempotent, and edits only after compatibility has passed.
- `apply()` returns at least `{"changed_files": [...]}` and may include `notes`.
- `changed_files` must exactly match the repo files actually changed during `--apply`.
- `TARGET_FILES` and `changed_files` must be repo-relative paths; absolute paths, `~`, and `..` traversal are rejected.
- Patches must not contain secrets, print secrets, dump environment variables, or store sensitive diffs.

`patchkeeper.py` reports a per-patch `status`, such as `present`, `missing_compatible`, `missing_incompatible`, `invalid_target_paths`, `applied`, `apply_failed`, `apply_incomplete`, `apply_unreported_changes`, `apply_misreported_changes`, `invalid_changed_files`, or `disabled`.

## Removing A Patch

When upstream incorporates a customization:

1. Confirm `is_present()` returns `true` on the updated Hermes checkout.
2. Remove or disable the local patch module.
3. Keep a short note in your project history explaining the upstream replacement.
4. Run `patchkeeper.py --check` again to confirm no missing local patch remains.

## Safety Model

Patch modules are trusted executable Python code. `--check` snapshots the repo before and after audit and exits with code `3` if it detects a mutation, but this is a guardrail, not a security sandbox. The default `--check` snapshot skips common heavy cache/build paths such as `.venv`, `node_modules`, `dist`, `build`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, and `.coverage`; pass `--full-snapshot` for a slower exhaustive audit. Symlinks are recorded as symlinks instead of hashing their targets.

`--apply` takes a before/after snapshot and compares the files actually changed against the patch module's reported `changed_files`. If a patch changes unreported files or reports files that did not change, the runner marks the patch as failed.

This project deliberately avoids fully automatic post-update hooks. The safe workflow is always:

1. Audit first.
2. Detect whether functionality already exists upstream.
3. Check compatibility anchors.
4. Report findings and dirty working tree risks.
5. Wait for explicit user authorization.
6. Apply only authorized compatible patches.
7. Report changed files and recommended verification tests.
