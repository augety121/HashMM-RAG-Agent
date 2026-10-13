# HashMM execution-plan template

Use this template for ambiguous, multi-file, security-sensitive or release work.
Planning is read-only; no modification or command may be described as completed
until execution evidence exists.

## Goal and boundaries

- Restate the concrete user outcome.
- List in-scope components and explicit non-goals.
- Record assumptions that could change the implementation.

## Evidence before changes

- Identify the current execution path, owner checks and safety boundaries.
- Locate existing tests and release gates.
- For current product or dependency claims, record the authoritative source.

## Steps

Each step must contain:

1. Action — one bounded change or inspection.
2. Files/surface — the exact expected area.
3. Side effect — `false` for inspection/planning, `true` for mutation.
4. Acceptance — a command, assertion, diff property or artifact that can be
   independently checked.

Order steps as understand → implement → targeted verification → adversarial or
authorization verification → full regression → release artifact verification.

## Safety and rollback

- Identify possible data loss, cross-user access, network or command execution.
- Explain how interrupted side effects are recovered without blind replay.
- Prefer additive migrations and atomic replacement; do not rely on model
  confidence as a rollback mechanism.

## Completion report

- Summarize actual changes with file references.
- Report exact tests, skipped cases, artifact version, size and SHA-256.
- Separate verified facts from estimates and remaining limitations.

