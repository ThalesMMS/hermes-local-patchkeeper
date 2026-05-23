# Patch Design Note

Use this note before converting a Hermes checkout or image-layer edit into a `post-hermes-update` patch module.

## Summary

- Patch id:
- Public-safe purpose:
- Classification: local-customization / upstreamable-local-patch
- Target files:

## Persistence Decision

- Why this is not normal persistent Hermes config/user state:
- Why this may be lost after a native update, git operation, or Docker image update:
- Why the behavior should survive future updates:

## Upstream Presence Check

Describe how `is_present(repo)` will detect that upstream already includes the desired behavior.

- Marker, behavior, or code shape to check:
- Files to inspect:
- False-positive risks:

## Compatibility Check

Describe the exact anchors `check_compatible(repo)` must find before edits.

- Required files:
- Required anchors:
- Conditions that should return incompatible:

## Apply Strategy

Describe the idempotent `apply(repo)` behavior.

- Edits to make:
- How repeat runs avoid duplicate edits:
- Changed files to report:

## Secret-Safety Review

- Confirm the patch module contains no tokens, credentials, private contacts, private profile names, or sensitive logs:
- Confirm the module does not print file contents, environment dumps, or diffs that may contain secrets:

## Verification

- Unit tests:
- Smoke tests:
- Manual checks:
