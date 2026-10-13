# HashMM V1100 Continuity Runtime Contract

## Product invariants

1. A non-archived conversation has one navigation owner: an unassigned Chat is
   visible in Recent; a project Chat is visible only under that project.
2. `content_activity_at` advances only for durable user-visible content or run
   activity. Metadata edits and sync observation have independent clocks.
3. New-Chat continuation, Chat mailbox delivery and same-Chat device resume are
   separate protocols. None carries tool approval or hidden model reasoning.
4. A Worktree belongs to one server project and one device-local Git binding.
   Cleanup is disabled by default and fails closed for tracked, untracked,
   ignored, active or detached-only work.
5. ProjectVault status distinguishes the configured path from the path used by
   a running local backend. Remote server data is never described as local.
6. Python plugin code requires exact-digest trust. Declarative integrations do
   not receive code trust; activation is separately bound to an exact digest.
7. A completed side-effect task requires a verified execution receipt. Model
   prose and an empty receipt list are not completion evidence.

## Compatibility and migration

- Existing `updated_at` remains a replication revision clock.
- Old rows receive content activity from existing durable records, never from
  migration wall-clock time.
- The deprecated combined handoff notification is projected once into three
  bounded notification keys without overwriting explicit user choices.
- Stored `general.followup_mode=queue` is migrated to `steer`; new queue writes
  are rejected until a durable run queue exists.
- Deprecated frontend aliases remain for one compatibility cycle, but all new
  code uses continuation and context-insert terminology.

## Acceptance gates

- Metadata changes preserve content time.
- 2,000+ conversation keyset pagination has no duplicate or omission.
- Cross-Chat payloads are owner/project scoped, idempotent and redacted.
- ProjectVault desired/effective mismatch reports restart required.
- Declarative plugin changes invalidate activation without executing code.
- Worktree removal cannot discard unproven user data.
- L3 rejects completed side effects without verified receipts.
