# HashMM V390–V425 contract audit (V428)

This audit records only repository evidence. A page, a capability label, or a
model response is not treated as proof that a feature is wired.

## Single user-facing chain

`Chat request → bounded context capsule → retrieval/evidence ledger → governed
tool call → execution receipt → causal/evidence graph → completion gate →
WorkRuntime projection → desktop/App read model`

The chain is implemented in these modules:

- V390–V391: `hashmm/agent/context_engine.py`,
  `hashmm/agent/execution_receipt.py`, `hashmm/agent/task_evidence_graph.py`,
  `hashmm/agent/causal_work_graph.py`, `hashmm/agent/evidence_ref.py`,
  `hashmm/api/routes/canvas_evidence.py`.
- V392: `hashmm/agent/mesh.py`, `hashmm/agent/team.py` and the durable mailbox
  tables. Worker results are independently bounded and cannot become a
  completion claim without the verifier/manifest gate.
- V393–V394: `hashmm/evolution/skill_evolver.py`,
  `hashmm/evolution/skill_replay.py` and the CAS/rollback tests. Candidates do
  not enter Chat until owner approval, replay and safety checks pass.
- V395–V400: `hashmm/agent/work_runtime.py` and
  `frontend-next/components/desktop/GlobalWorkspaceView.tsx`. The canvas is a
  deterministic projection of plan, evidence, results, decisions and receipts;
  it never executes an action.
- V401–V410: Electron session vault/CAS refresh, context compaction, provider
  capability negotiation and cross-client projections.
- V411–V420: owner-scoped remote sessions, short-lived tickets, HTTPS/WSS
  gates, WebRTC/MJPEG fallback, durable remote audit and TURN readiness checks.
- V421–V425: action inbox, transport summaries, handoff lineage, completion
  receipts and predecessor invalidation in
  `hashmm/api/remote_sessions.py`, `hashmm/api/remote_work.py` and
  `hashmm/api/remote_persistence.py`.

## V428 hardening completed in this pass

1. The historical `SmartAgent` compatibility import now delegates to the real
   governed `ReactAgent`. Raw two-argument tool callbacks and unscoped worker
   callables are rejected; multi-agent fan-out is bounded and reports
   `completed`, `partial`, or `failed` instead of fabricating a synthesis.
2. A durable loop no longer claims cross-device sync when WorkRuntime admission
   fails. The loop exposes `linked`, `degraded`, or `not_configured` together
   with a bounded error, and the desktop work surface renders that state.
3. Loop transitions use deterministic WorkRuntime idempotency keys and the
   atomic `append_event_once` path. Reconnects cannot advance the shared event
   cursor twice.
4. Graph RAG and sandbox capabilities are advertised as wired only when the
   actual Chat tool and isolated execution backend are present. Data existing
   on disk is not treated as a mounted capability.

## Deterministic evidence

The deterministic evidence is intentionally grouped by contract rather than
pretending that every minor version has a separate test file:
`tests/test_v390_evidence_fabric.py`,
`tests/test_v391_browser_canvas_twin.py`,
`tests/test_v392_agent_mesh.py`,
`tests/test_v393_governed_skill_evolution.py`,
`tests/test_v394_skill_replay_gate.py`,
`tests/test_v395_work_presentation.py`,
`tests/test_v400_work_canvas.py`,
`tests/test_v421_remote_work.py` and
`tests/test_v425_remote_continuity.py`, supplemented by the provider,
context, persistence, Electron and frontend suites. V396–V399, V401–V420
and V422–V424 are covered by those shared contracts and the full suite rather
than by invented per-version files. V428 compatibility/binding coverage is in
`tests/test_smart_agent_compat.py`, `tests/test_v368_runtime_capabilities.py`
and `tests/test_v428_runtime_binding.py`. The release gate also checks the
frontend, Electron security contracts and the native source package.

## Environment-dependent acceptance

The repository cannot honestly claim public two-device symmetric-NAT success,
TURN relay coverage, a 24-hour soak, third-party API quotas, or Authenticode
publisher identity. Those require a deployed acceptance run and a stored
receipt. No secret, service-role key, token, or rendered `.env` is included in
the server package.
