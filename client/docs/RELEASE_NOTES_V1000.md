# HashMM V1000 release notes

Release date: 2026-08-09  
Backend/SDK: `V1000 / 1.0.0`  
Desktop/WebUI/MCP/Native: `3.0.0`

V1000 makes conversation placement server-authoritative, separates project Chats from unassigned Recent Chats, adds stable keyset history pagination and deletion tombstones, and prevents stale cloud rows from resurrecting deleted or moved conversations.

Verified Chat handoff transfers a public task capsule with revision, artifact hashes and WorkRun/checkpoint references. It excludes private reasoning, raw tool arguments, secrets and permissions. Chat mailbox adds owner/project-scoped idempotent delivery and redaction at the API/SDK layer.

WorkRuntime now publishes task-transition lineage and distinguishes `completed_with_limits`. Settings expose desired/effective/source/revision/availability facts, and the existing administration pages are grouped into ten governance domains without replacing backend authorization.

Known limits: Chat mailbox does not yet have the full recipient-discovery/inbox UI; follow-up queue is not durable across restarts; Workspace/Git freshness needs desktop-side verification; the Windows installer is SHA-256 verified but currently not Authenticode signed. No pip dependency was added or upgraded in this release.

The normative implementation and acceptance contract is `docs/CONTINUITY_CONTROL_PLANE_V1000_SPEC.md`.
