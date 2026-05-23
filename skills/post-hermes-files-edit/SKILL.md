---
name: post-hermes-files-edit
description: Classify edits to non-persistent Hermes Agent files and convert durable local customizations into patch modules. Use after changing Hermes checkout files, Docker image-layer files, or other non-persistent Hermes source files that may be lost on update.
version: 2.0.0
author: Hermes local patchkeeper
license: MIT
metadata:
  hermes:
    tags: [hermes, local-patches, file-edits, patchkeeper, source-checkout]
---

# post-hermes-files-edit

Use this skill after a user edits Hermes Agent files that may be replaced by `hermes update`, a git reset, a native reinstall, or a Docker image update.

Do not use it for persistent user state such as `$HERMES_HOME`, `~/.hermes/config.yaml`, `.env`, auth stores, memories, cron definitions, normal custom skills, or Docker volumes such as `/opt/data`.

## Classification

Inspect changed files with commands like:

```bash
git status --short
git diff --stat
git diff --name-status
```

Classify each edit as:

- `persistent-user-state`: lives in `$HERMES_HOME`, `~/.hermes`, or a Docker volume; no patch module needed.
- `temporary-discardable`: experiment or debug-only change; no patch module needed.
- `local-customization`: important source/image change that should survive updates; create a patch module.
- `upstreamable-local-patch`: should be proposed upstream, but needs a local patch until merged.

## Capture Workflow

For each durable local customization, record:

- short patch id;
- target files;
- public-safe purpose;
- how to detect that upstream already includes the behavior;
- exact anchors required for compatibility;
- idempotent apply steps;
- tests or smoke checks;
- secret-safety review.

Use the design note template:

```bash
cp skills/post-hermes-files-edit/templates/patch_design_note.md /tmp/my_patch_design_note.md
```

Then create a module:

```bash
POST_HERMES_UPDATE_DIR=/path/to/post-hermes-update
cp "$POST_HERMES_UPDATE_DIR/templates/patch_template.py" "$POST_HERMES_UPDATE_DIR/scripts/patches/my_patch.py"
$EDITOR "$POST_HERMES_UPDATE_DIR/scripts/patches/my_patch.py"
python "$POST_HERMES_UPDATE_DIR/scripts/patchkeeper.py" --repo "$HERMES_HOME/hermes-agent" --check --only my-patch-id
```

## Patch Requirements

Every patch module must:

- keep `--check` read-only; patchkeeper will fail with exit code `3` if it detects repo mutation during check;
- require explicit `--apply`;
- be idempotent;
- detect upstream presence before patching;
- check exact compatibility anchors before editing;
- avoid fuzzy edits when anchors are missing;
- use only repo-relative `TARGET_FILES` and `changed_files`; absolute paths, `~`, and `..` traversal are rejected;
- ensure `changed_files` exactly matches files actually modified during `--apply`;
- avoid secrets, tokens, private identifiers, environment dumps, and sensitive diffs;
- report changed files and recommended tests.

## Updating post-hermes-update

Add only generic, user-owned patch modules under `skills/post-hermes-update/scripts/patches/`. Do not hard-code private operational rules in `SKILL.md`; keep patch-specific behavior inside the module with public-safe descriptions.

When upstream incorporates a customization, confirm `is_present()` returns true, then disable or remove the local patch module.
