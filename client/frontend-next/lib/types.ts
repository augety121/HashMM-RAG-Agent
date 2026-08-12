export interface Source { rank: number; chunk_id: string; doc_id: string; modality: string; text: string; score: number; method?: string; id?: number; filename?: string; page?: number; section?: string; graph_support?: { entities: string[]; relations: string[] } | null; }
export interface GroundingEvidence { source_index: number; source_id: string; chunk_id?: string; doc_id?: string; filename?: string; page?: number; section?: string; score?: number; overlap?: number; }
export interface GroundingClaim { id: string; claim_span: { start: number; end: number; text: string }; status: "supported" | "inferred" | "unsupported" | "invalid_citation"; citations: number[]; invalid_citations?: number[]; evidence: GroundingEvidence[]; reason: string; }
export interface GroundingLedger { version: number; status: "passed" | "failed" | "not_evaluable"; validation_method: string; semantic_entailment_verified: false; disclaimer: string; total_factual_claims: number; supported_claims: number; inferred_claims: number; unsupported_claims: number; invalid_citation_claims: number; invalid_citations: number[]; coverage_ratio: number | null; review_required: boolean; truncated?: boolean; claims: GroundingClaim[]; }
export type WorkRunStatus = "created" | "queued" | "running" | "waiting_input" | "waiting_approval" | "paused" | "retrying" | "verifying" | "blocked" | "observed" | "delivered" | "completed" | "failed" | "cancelled" | "interrupted";
export type WorkControlAction = "pause" | "resume" | "cancel" | "retry";
  export interface WorkOperatingProjection {
    schema: "hashmm.user-operating-projection.v1";
    method_label: string;
    route_state: "ready" | "setup_required";
    route_label: string;
    reason: string;
    requires_confirmation: boolean;
    can_resume: boolean;
    evidence_required: boolean;
    revision?: string;
  }
  export interface WorkPresentation {
  schema: "hashmm.work-presentation.v1";
  title: string; category: string; status_label: string;
  phase: "preparing" | "working" | "needs_user" | "needs_attention" | "review" | "finished";
  current_step: string; next_action: string; needs_user: boolean;
  primary_action: "open" | "answer" | "review_approval" | "review_delivery" | "resume" | "retry" | "inspect";
  progress: { mode: "criteria" | "phase"; completed: number; total: number; label: string };
    deliverables: Array<{ name: string; type: string }>;
    evidence: { status: string; count: number; label: string };
    sync: { state: "synced"; updated_at: number };
    operating?: WorkOperatingProjection;
  }
  export interface WorkCanvasStage {
    id: string; order: number; label: string;
    status: "pending" | "running" | "done" | "blocked" | "skipped";
  }
  export interface WorkCanvasCheckpoint {
    id: string; label: string; created_at: number;
    kind: "context" | "execution"; can_resume: boolean;
  }
  export interface WorkCanvasResult {
    schema: "hashmm.work-result.v1"; id: string; name: string;
    kind: "document" | "pdf" | "presentation" | "spreadsheet" | "canvas" | "image" | "text" | "file";
    version: number; version_ref: string;
    verification: "missing" | "stale" | "verified" | "ready" | "reported";
    size: number; download_url: string; actions: string[]; evidence_node_id: string; updated_at: number;
  }
  export interface WorkCompletionReceipt {
    schema: "hashmm.completion-receipt.v1"; receipt_id: string; run_id: string;
    status: "blocked" | "verified" | "accepted_with_limits" | "awaiting_review" | "completed_with_limits" | "not_ready";
    can_claim_complete: boolean; can_claim_verified: boolean;
    summary: { results: number; sources: number; checks: number; side_effects: number; irreversible_side_effects: number };
    verification: { status: string; criteria: Array<Record<string, unknown>>; next_action: string };
    user_review: { status: "accepted" | "pending"; authority: string; decided_at: number };
    results: Array<{ id: string; name: string; version_ref: string; verification: string }>;
    side_effects: Array<{ tool: string; status: string; class: string; reversible: boolean }>;
    limitations: string[];
    recovery: { checkpoints: number; can_resume: boolean; can_retry: boolean; automatic_rollback_available: boolean };
    completed_at: number;
    integrity: { algorithm: "sha256"; content_hash: string; model_prose_is_completion_evidence: false };
  }
  export interface GovernedNextAction {
    id: string; kind: "control" | "recommendation" | "review";
    label: string; reason: string; risk: "low" | "medium" | "high";
    within_scope: boolean; requires_confirmation: boolean;
    control_action: WorkControlAction | ""; auto_execute: false;
  }
  export interface WorkAssurance {
    schema: "hashmm.work-assurance.v1";
    versions: Record<"recovery" | "context" | "evidence" | "artifacts" | "delegation" | "authority" | "provider" | "sync" | "delivery", string>;
    user_summary: {
      state: "verified" | "attention" | "working" | "not_verified";
      headline: string; detail: string; checks_observed: number; checks_total: number;
    };
    recovery: { version: string; status: string; can_resume: boolean; checkpoint_count: number };
    context: { version: string; status: string; estimated_tokens: number; max_input_tokens: number; remaining_tokens: number; pressure: number | null; compaction_observed: boolean };
    evidence: { version: string; status: string; required: number; passed: number; failed: number; coverage: number | null };
    artifacts: { version: string; status: string; revision_count: number; result_count: number; stale_result_ids: string[]; content_addressed: boolean };
    delegation: { version: string; status: string; strategy: string; agents: number; running: number; blocked_node_ids: string[] };
    authority: { version: string; status: string; approval_mode: string; network_mode: string; pending_approval: boolean; receipt_count: number; widens_scope: false };
    provider: { version: string; status: string; compatible: boolean | null; provider_id: string; missing: string[] };
    sync: { version: string; status: string; revision: number; event_cursor: number; change_cursor: number; fingerprint: string; owner_bound: boolean };
    delivery: { version: string; status: string; can_deliver: boolean; receipt_allows_completion: boolean; blockers: Array<{ code: string; area: string }> };
    integrity: { projection_only: true; auto_executes: false; widens_scope: false; model_prose_is_evidence: false; fingerprint: string };
  }
  export interface WorkExecutionLease {
    id: string; state: string; holder_type: string; holder_id: string;
    generation: number; expires_at: number;
  }
  export interface WorkExecutionDevice {
    device_id: string; name: string; runner: string; version: string;
    online: boolean; last_seen: number;
  }
  export interface WorkArtifactAnnotation {
    id: string; artifact_id: string; artifact_revision: number;
    target: Record<string, unknown>; note: string; status: string;
    impacted_result_ids: string[]; created_at: number;
  }
  export interface WorkProjection {
    schema: "hashmm.work-projection.v2";
    identity: { work_id: string; project_id: string; conversation_id: string; domain: "work" };
    contract: {
      goal: string; goal_source: string; constraints: string[];
      completion_criteria: Array<{ id: string; label: string; required: boolean }>;
    };
    status: {
      label: string; phase: string; current_step: string; needs_user: boolean;
      main_action: string; main_action_label: string;
    };
    placement: {
      schema: "hashmm.execution-placement.v1"; kind: "local" | "remote_device" | "cloud" | "waiting_device";
      target_id: string; label: string; state: string; requested_by: string;
      authority_expanded: false; lease?: WorkExecutionLease;
    };
    capability_plan: {
      schema: "hashmm.capability-plan.v1"; selected: "structured" | "browser" | "computer" | "manual";
      label: string; requires_confirmation: boolean; reason: string;
      considered: Array<{ kind: string; ready: boolean; reason: string; requires_confirmation: boolean }>;
      precedence: string[];
      integrity: { server_facts_only: true; model_selected: false; widens_scope: false; computer_use_is_fallback: boolean };
    };
    work_twin: {
      schema: "hashmm.work-twin.v1";
      nodes: Array<{ id: string; kind: string; label: string; status: string; version_ref?: string }>;
      edges: Array<{ from: string; to: string; kind: string }>;
      summary: { goals: number; criteria: number; tasks: number; evidence: number; artifacts: number; stale: number };
      integrity: { explicit_edges_only: true; model_inferred_edges: false; projection_only: true };
    };
    annotations: WorkArtifactAnnotation[];
    recovery: {
      schema: "hashmm.recovery-center.v1";
      conversation: { available: boolean; checkpoint_count: number; latest: WorkCanvasCheckpoint | null };
      files: { available: boolean; previous_versions: number };
      execution: { available: boolean; checkpoint_count: number; latest: WorkCanvasCheckpoint | null };
      automatic_rollback_available: boolean;
    };
    workflow_candidate: {
      schema: "hashmm.workflow-candidate.v1"; id?: string; revision: number; status: string; demonstration_verified: boolean;
      receipt_hash: string; used_skill_versions: string[]; can_publish: boolean; required_next_step: string;
    };
    proactive_inbox: {
      schema: "hashmm.proactive-inbox.v1"; count: number; budget: Record<string, unknown>;
      items: Array<{
        id: string; label: string; reason: string; risk: string;
        action?: string; status?: string; requires_confirmation: boolean;
        cancelable: boolean; auto_execute: false;
      }>;
    };
    collaboration: {
      schema: "hashmm.agent-collaboration.v1"; strategy: string;
      workers: Array<{ id: string; label: string; status: string }>; blocked: string[];
      write_isolation: { status: "verified" | "not_proven"; mode: string; integrator_only_merge: boolean; verifier_read_only: boolean };
      user_summary: string;
    };
    autonomy: {
      schema: "hashmm.autonomy-profile.v1"; requested_level: number; released_level: number;
      label: string; evaluation_receipt_valid: boolean; suite_hash: string; a5_available: false;
    };
    integrity: {
      projection_only: true; owner_check_required: true;
      model_prose_is_execution_evidence: false; model_prose_is_verification_evidence: false;
      fingerprint: string;
    };
  }
  export interface WorkCanvas {
    schema: "hashmm.work-canvas.v1"; run_id: string; conversation_id: string;
    overview: {
      title: string; category: string; status_label: string; phase: WorkPresentation["phase"];
      goal: string; current_step: string; next_action: string;
      progress: WorkPresentation["progress"]; needs_user: boolean;
      primary_action: WorkPresentation["primary_action"];
    };
    process: {
      stages: WorkCanvasStage[];
      branches: Array<{ id: string; label: string; status: WorkCanvasStage["status"] }>;
      checkpoints: WorkCanvasCheckpoint[]; event_count: number;
      latest_events: Array<{ id: string; type: string; summary: string; status: string; created_at: number }>;
    };
    evidence: {
      status: string;
      sources: Array<{ id: string; kind: string; label: string; status: string; trust: string; revision: number }>;
      checks: Array<{ id: string; label: string; status: string; detail: string }>;
      execution_receipts: Array<{ id: string; tool: string; status: string; success: boolean; side_effect: string; reversible: boolean; valid: boolean }>;
      summary: { sources: number; checks: number; receipts: number; invalid_receipts: number; stale_nodes: number };
      invalidation: { strategy: string; stale_node_ids: string[] };
    };
    results: WorkCanvasResult[];
    completion_receipt: WorkCompletionReceipt;
    next_actions: {
      schema: "hashmm.governed-next-actions.v1"; items: GovernedNextAction[];
      governance: {
        scope_id: string; approval_mode: string; network_mode: string;
        budgets: Record<string, unknown>; high_risk_observations: number;
        auto_execution_enabled: false; widens_scope: false;
      };
      limitation: string;
    };
    learning: {
      schema: "hashmm.skill-learning-projection.v1";
      used_versions: Array<{
        id: string; name: string; scope: string; version_ref: string;
        evolution_id: string; status: "used";
      }>;
      governance: {
        records_exact_version: true; prompt_body_exposed: false;
        automatic_promotion_allowed: false; owner_bound_feedback_required: true;
        paired_replay_required_before_promotion: true;
        safety_regression_blocks_promotion: true;
      };
      limitation: string;
    };
    protocol: {
      schema: "hashmm.work-protocol.v4"; owner_bound: true; owner_ref: string;
      run_id: string; fingerprint: string;
      source_bodies_included: false; private_reasoning_included: false;
      spec: {
        schema: "hashmm.work-spec.v1"; goal: string; deliverable: string;
        criteria: string[]; constraints: string[]; permission_mode: string;
        project_id: string; fingerprint: string;
      };
      actions: Array<{
        schema: "hashmm.work-action.v1"; action_id: string; kind: string;
        summary: string; actor: string; status: string; side_effect: string;
        approval_id: string; fingerprint: string;
      }>;
      observations: Array<{
        schema: "hashmm.work-observation.v1"; observation_id: string;
        action_id: string; source: string; summary: string; status: string;
        measured: Record<string, number | boolean>; fingerprint: string;
      }>;
      evidence: Array<{
        schema: "hashmm.evidence-receipt.v1"; receipt_id: string; claim: string;
        observation_ids: string[]; verification: string; verifier: string;
        fingerprint: string;
      }>;
    };
    change_impact?: {
      schema: "hashmm.change-impact.v1"; requested: boolean; reason: string;
      impacted_result_ids: string[]; impacted_stage_ids: string[]; next_action: string;
      integrity: {
        construction: string; model_inferred_impact: false; auto_executes: false;
      };
    };
    assurance: WorkAssurance;
    operating: WorkOperatingProjection;
    product?: WorkProjection;
    sync: {
      revision: number; event_cursor: number; change_cursor: number;
      etag: string; updated_at: number; active_generation_id: string;
      generation_hash: string; invalidated_result_ids: string[];
    };
    integrity: {
      projection_only: true; auto_executes: false; widens_scope: false;
      model_prose_is_evidence: false; owner_check_required_by_api: true;
    };
    latest_decision?: WorkDecision;
  }
  export interface WorkDecision {
    schema: "hashmm.work-decision.v1"; id: string; run_id: string;
    action: "accept_delivery" | "request_changes"; expected_revision: number;
    status: "applied"; note: string; result: Record<string, unknown>; created_at: number;
  }
  export interface WorkActionItem {
  schema: "hashmm.action-item.v1"; id: string; run_id: string; conversation_id: string;
  type: "approval" | "question" | "blocker" | "failure" | "interruption" | "delivery";
  priority: "high" | "normal"; title: string; summary: string;
  primary_action: WorkPresentation["primary_action"];
  context: {
    kind?: "remote_session"; session_id?: string; state?: string;
    generation?: number; scopes?: string[]; predecessor_session_id?: string;
  };
  updated_at: number;
}
export interface WorkCommand {
  schema: "hashmm.work-command.v1"; id: string; run_id: string;
  action: WorkControlAction; expected_revision: number;
  status: "executing" | "applied" | "failed" | "uncertain";
  result: Record<string, unknown>; created_at: number; finished_at: number;
}
export interface WorkControl {
  schema: "hashmm.work-control.v1"; expected_revision: number;
  available_actions: WorkControlAction[];
  side_effect_boundary: "checkpointed_cooperative" | "cooperative_after_current_model_call" | "cancel_before_desktop_claim_only" | "observe_only";
  latest_command?: WorkCommand;
}
export interface WorkEvent {
  schema: "hashmm.work-event.v1"; id: string; run_id: string; seq: number;
  type: string; status: WorkRunStatus | ""; summary: string;
  payload: Record<string, unknown>; created_at: number;
  idempotency_key?: string; expected_revision?: number; generation_id?: string;
  envelope?: WorkEventEnvelope;
}
export interface WorkEventEnvelope {
  schema: "hashmm.event.v1"; event_id: string; stream_id: string;
  event_seq: number; schema_version: "hashmm.event.v1";
  actor: { type: "user" | "agent" | "device" | "system"; id: string };
  type: string; idempotency_key: string; trace_id: string; occurred_at: number;
  payload_ref: { kind: "inline_projection" | "artifact"; content_redacted: boolean; [key: string]: unknown };
}
export interface WorkGeneration {
  schema: "hashmm.work-generation.v1"; id: string; run_id: string;
  generation: number; status: "active" | "superseded";
  manifest_hash: string; manifest: Record<string, unknown>;
  created_at: number; activated_at: number;
}
export interface WorkArtifactRevision {
  schema: "hashmm.artifact-revision.v1"; id: string; artifact_id: string;
  run_id: string; revision: number; content_hash: string; media_type: string;
  size_bytes: number; locator: Record<string, unknown>;
  verification: "pending" | "reported" | "ready" | "verified" | "stale" | "missing" | "failed";
  created_at: number;
}
  export interface WorkRun {
  schema: "hashmm.work-run.v1"; id: string; user_id: string;
  conversation_id: string; kind: "chat" | "loop" | "team" | "agent_session" | "browser" | "computer" | "remote" | "artifact" | "workflow";
  source_id: string; title: string; status: WorkRunStatus;
  revision: number; event_cursor: number; change_cursor: number;
  project_id?: string;
  execution_target?: WorkProjection["placement"];
  autonomy_level?: number;
  active_lease?: WorkExecutionLease | null;
  annotations?: WorkArtifactAnnotation[];
  active_generation_id?: string; active_generation?: WorkGeneration;
  artifact_revisions?: WorkArtifactRevision[];
  control: WorkControl;
    task_state?: { schema: string; state: WorkRunStatus; terminal?: boolean; allowed_next?: string[]; requires_user?: boolean };
    presentation: WorkPresentation;
    snapshot: Record<string, unknown>; created_at: number; updated_at: number;
  workspace?: WorkCanvas;
    checkpoints?: Array<{ id: string; generation: number; reason: string; created_at: number; state?: Record<string, unknown> }>;
    events?: WorkEvent[]; events_truncated?: boolean;
  }
  export interface WorkCommandResponse {
  ok: boolean; duplicate?: boolean; error?: string;
  current_revision?: number; command?: WorkCommand; run?: WorkRun;
  }
  export interface WorkDecisionResponse {
    ok: boolean; duplicate?: boolean; error?: string; reason?: string;
    current_revision?: number; decision?: WorkDecision; run?: WorkRun;
  }
export interface WorkFeed {
  schema: "hashmm.work-feed.v1"; items: WorkRun[]; after_cursor: number;
  next_cursor: number; high_water_cursor: number; has_more: boolean;
  action_inbox: { schema: "hashmm.action-inbox.v1"; items: WorkActionItem[]; count: number; high_priority_count: number };
}
export interface RunVerificationCheck { id: string; status: "passed" | "failed" | "not_evaluable"; detail: string; evidence?: Record<string, unknown>; }
export interface RetrievalRun {
  schema: "hashmm.retrieval-run.v1"; run_id: string;
  status: "completed" | "empty" | "degraded" | "failed" | "skipped";
  requested_mode: string; resolved_mode: string;
  route: { reason: string; hops: number }; requested_top_k: number; acl_scoped: boolean;
  attempts: Array<{ stage: string; query: string; result_count: number; top_score: number | null; selected: boolean }>;
  selected_query: string;
  filters: Array<{ stage: string; before: number; after: number; removed: number; reason: string }>;
  degradations: Array<{ stage: string; reason: string }>;
  evidence_count: number; total_candidates: number; elapsed_ms: number;
  started_at: number; model_self_grade: false;
}
export interface TaskContract {
  schema: string; run_id?: string; conversation_id?: string; goal: string; goal_source: "user_message"; task_type: string; execution_mode: string;
  requires_plan: boolean; method: string; evidence_policy: "runtime_facts_only";
  success_criteria: Array<{ check_id: string; label: string; required: boolean }>;
  plan: Array<{ text: string; status: string }>;
}
export interface RunHandoff {
  status: "checks_passed" | "delivered_with_limits" | "needs_attention" | "blocked";
  summary: string; passed_checks: string[]; failed_checks: string[]; not_evaluable_checks: string[];
  next_action: string; limitation: string;
}
export interface AgentHarnessEvent {
  seq: number; offset_ms: number; type: string; status?: string; tool?: string;
  args_hash?: string; detail?: Record<string, string | number | boolean>;
}
export interface AgentHarness {
  schema: "hashmm.agent-harness.v1";
  context: {
    schema: "hashmm.turn-context.v1"; run_id: string; owner_fingerprint: string;
    conversation_id: string; goal_fingerprint: string; scope_id: string;
    parent_scope_id: string; depth: number; approval_mode: string; network_mode: string;
    allow_subagents: boolean;
    budgets: { max_iterations: number; max_tool_calls: number; max_search_calls: number; max_exec_calls: number; max_workers: number };
    capability_revision: string; effective_tools: string[];
  };
  capabilities: {
    schema: "hashmm.capability-snapshot.v1"; revision: string;
    declared_count: number; effective_count: number;
    effective_tools: string[]; missing_executors: string[];
  };
  trajectory: { event_count: number; event_types: Record<string, number>; events: AgentHarnessEvent[]; truncated: boolean };
  children: { active: number; total: number; limit: number };
  terminal: { schema?: "hashmm.terminal-outcome.v1"; reason?: string; status?: string; stop_reason?: string; started_at?: number; ended_at?: number; error?: string };
  limitation: string;
  levels?: { schema: string; claim: string; passed?: number; total?: number; levels: Array<{ level: string; name: string; status: string; [key: string]: unknown }> };
}
export interface ToolApprovalRequest {
  schema: "hashmm.tool-approval.v1"; request_id: string; conversation_id: string;
  message_id: string; tool_name: string; arguments: Record<string, unknown>;
  cwd: string; reason: string; risk: string;
  status: "pending" | "approved" | "declined" | "consumed" | "expired" | "cancelled";
  created_at: number; expires_at: number;
}
export interface TaskEvidenceGraphNode {
  id: string; kind: "goal" | "criterion" | "scope" | "source" | "claim" | "tool" | "agent" | "artifact" | "check" | string;
  label: string; status: string; meta?: Record<string, unknown>;
}
export interface TaskEvidenceGraphEdge { source: string; target: string; relation: string; meta?: Record<string, unknown>; }
export interface TaskEvidenceGraph {
  schema: "hashmm.task-evidence-graph.v1"; graph_id: string; run_id: string;
  status: "ready" | "partial" | "blocked"; root_node_id: string;
  summary: { nodes: number; edges: number; counts: Record<string, number>; blockers: number; open_nodes: number; claim_evidence_coverage: number | null };
  nodes: TaskEvidenceGraphNode[]; edges: TaskEvidenceGraphEdge[];
  blockers: Array<{ node_id: string; reason: string }>;
  critical_node_ids: string[]; next_actions: string[];
  integrity: { construction: "deterministic_runtime_facts"; model_inferred_edges: 0; owner_data_included: false; bounded: true };
  limitation: string;
}
export interface ExecutionFrontierRoute {
  kind: "tool" | "strategy"; tool?: string; strategy?: string;
  status: "ready" | "unavailable" | "requires_scope" | "blocked_by_scope" | "waiting_input";
  reason: string;
}
export interface ExecutionFrontierItem {
  id: string; blocker_node_id: string;
  category: "evidence_gap" | "delivery_gap" | "delegation_gap" | "tool_recovery" | "verification_gap" | "review_gap" | string;
  label: string; desired_state: "resolved"; observed_state: string; reason: string;
  working_set: { id: string; node_ids: string[]; construction: "query_time_runtime_neighbourhood" };
  candidates: ExecutionFrontierRoute[]; selected_route: ExecutionFrontierRoute; minimum_action: string;
}
export interface ExecutionFrontier {
  schema: "hashmm.execution-frontier.v1"; frontier_id: string; graph_id: string; run_id: string;
  status: "converged" | "actionable" | "waiting" | "unavailable";
  summary: { unresolved: number; ready_routes: number; scope_blocked: number; waiting_input: number };
  items: ExecutionFrontierItem[];
  reconciliation: { status: "initial" | "progressed" | "regressed" | "converged" | "changed" | "no_change"; closed_blocker_node_ids: string[]; opened_blocker_node_ids: string[] };
  integrity: { construction: "deterministic_graph_and_capabilities"; auto_executes: false; widens_scope: false; model_selected_routes: 0; bounded: true };
  limitation: string;
}
export interface CompletionGateCriterion {
  check_id: string; label: string; required: boolean; source: string;
  status: "passed" | "failed" | "not_evaluable" | string;
  authority: string; detail: string;
}
export interface CompletionGate {
  schema: "hashmm.completion-gate.v1"; gate_id: string; run_id: string;
  status: "verified" | "delivered_with_limits" | "incomplete" | "blocked" | "unavailable";
  can_claim_complete: boolean; can_claim_verified: boolean;
  decision: { method: string; terminal: boolean; termination_reason: string; previous_status?: string; changed?: boolean };
  summary: { required: number; passed: number; failed: number; review: number; missing: number; graph_blockers: number; ready_routes: number; scope_blocked: number; stale_nodes?: number; invalid_receipts?: number };
  criteria: CompletionGateCriterion[];
  failure_modes: Array<{ code: string; severity: "blocking" | "review" | "warning" | string; detail: string }>;
  trajectory: { tool_calls: number; unique_tool_calls: number; failed_tool_calls: number; open_tool_calls: number; repeated_call_groups: number; max_identical_repeats: number; agents: number; failed_agents: number; open_agents: number };
  next_action: string;
  integrity: { model_self_report_is_evidence: false; model_judge_can_accept_for_user: false; auto_executes: false; widens_scope: false; bounded: true; causal_freshness_enforced?: boolean };
  limitation: string;
}
export interface ExecutionReceipt {
  schema: "hashmm.execution-receipt.v1"; receipt_id: string; run_id: string; call_id: string;
  action: { tool: string; arguments_hash: string; scope_id: string; capability_revision: string };
  executor: { kind: string; name: string; version: string; device_ref: string };
  timing: { started_at: number; finished_at: number; elapsed_ms: number };
  permission: { decision: string; authority: string; approval_ref: string };
  outcome: { status: "completed" | "failed" | "denied"; success: boolean; result_hash: string; error_code: string };
  side_effect: { class: "none" | "observe" | "local_write" | "external_write" | "privileged_control"; external: boolean; reversible: boolean; idempotency_ref: string; compensation: string; compensation_status: string };
  risk: { score: number; level: "low" | "medium" | "high" | "critical"; required_approval: "none" | "policy" | "explicit"; factors: Record<string, number>; formula: string };
  evidence_refs: string[];
  artifacts: Array<{ ref: string; name: string; content_hash?: string; revision?: string; size?: number }>;
  verification: Array<{ check_id: string; status: string; evidence_ref: string }>;
  integrity: { algorithm: "sha256"; content_hash: string; raw_arguments_included: false; raw_result_included: false };
}
export interface ContextCapsule {
  schema: "hashmm.context-capsule.v1"; fingerprint: string; generation: number;
  conversation_revision: string; active_goal: string;
  criteria: Array<{ id: string; text: string; status: string; required: boolean }>;
  decisions: Array<{ id: string; text: string; status: string; required: boolean }>;
  blockers: Array<{ id: string; text: string; status: string; required: boolean }>;
  provider_profile: Record<string, unknown>;
  sections: Array<{ key: string; trust: string; hit: boolean; chars: number; content_hash: string; truncated: boolean; evicted: boolean; citation_anchor: string; compressible: boolean }>;
  rendered_hash: string; rendered_chars: number; source_bodies_included: false;
}
export interface CausalWorkGraphNode {
  id: string; semantic_key: string; kind: string; label: string; status: string;
  content_hash: string; generation_id: string; revision: number; observed_at: number;
  valid_from: number; valid_to: number | null; trust: string; sensitivity: string;
  payload: Record<string, unknown>; status_before_invalidation?: string; stale_because?: string[];
}
export interface CausalWorkGraph {
  schema: "hashmm.causal-work-graph.v1"; graph_id: string; run_id: string;
  generation_id: string; previous_generation_id: string;
  status: "ready" | "stale" | "invalid";
  nodes: CausalWorkGraphNode[];
  edges: Array<{ source: string; target: string; relation: string; metadata?: Record<string, unknown> }>;
  summary: { nodes: number; edges: number; counts: Record<string, number>; receipts: number; invalid_receipts: number; stale_nodes: number };
  invalidation: { changed_semantic_keys: string[]; observed_changed_semantic_keys?: string[]; added_semantic_keys: string[]; removed_semantic_keys: string[]; stale_node_ids: string[]; strategy: string };
  integrity: { construction: string; model_inferred_edges: 0; raw_tool_arguments_included: false; raw_tool_results_included: false; source_bodies_included: false; content_addressed: true; bounded: true };
  limitation: string;
}
export interface RunManifest {
  schema: string; run_id: string; release: string; task_type: string; execution_mode: string; model: string;
  retrieval: { mode: string; depth: string; strategy: string; fingerprint: string; top_k?: number; hybrid_mode?: string; rrf_k?: number; embedding_model?: string; graph_engineering?: { evidence_sources: number; evidence_preserving: boolean }; run?: RetrievalRun; result_contract?: { schema?: string; requested_top_k?: number; total_candidates?: number; evidence_count?: number; unique_documents?: number; citation_ready_count?: number; citation_ready?: boolean; filtered_count?: number; rerank_method?: string; source_methods?: Record<string, number>; graph?: Record<string, number> }; };
  retrieval_diagnostics?: { schema: string; status: "clear" | "attention" | "not_evaluable"; observed: Array<{ id: string; severity: string; evidence: string; action: string }>; not_evaluable: Array<{ id: string; reason: string }>; integrity: { model_self_report_used: false; causes_inferred_without_signal: false } };
  corpus_snapshot: { status: "pinned" | "evidence_only" | "unavailable"; id: string; vectors?: number; embedding_fingerprint?: string; built_at?: number; note?: string };
  evidence_snapshot: { id: string; sources: number };
  stage_latency_ms: Record<string, number>;
  termination: { reason: string; iterations: number };
  tokens: { input: number; output: number; estimated: boolean };
  task_contract?: TaskContract;
  evidence_graph?: TaskEvidenceGraph;
  causal_work_graph?: CausalWorkGraph;
  execution_receipts?: ExecutionReceipt[];
  execution_frontier?: ExecutionFrontier;
  completion_gate?: CompletionGate;
  harness?: AgentHarness;
  process?: {
    schema: "hashmm.public-process.v1";
    goal: string;
    method: "inspect_plan_act_verify_handoff";
    timeline: Array<{ id: string; kind: string; node: string; detail: string; tool?: string; status: string; elapsed_ms?: number }>;
    todo: Array<{ text: string; status: string }>;
    completion_status: string;
    reasoning_disclosed: false;
    integrity: { source: "runtime_manifest"; raw_model_reasoning_included: false; raw_tool_arguments_included: false; persisted: true };
  };
  work_loop?: {
    schema: "hashmm.agent-work-loop.v1";
    method: "understand_execute_validate_deliver_capture";
    dimensions: Array<{
      id: "task_understanding" | "controlled_execution" | "change_validation" | "reliable_delivery" | "learning_capture";
      label: string;
      status: "observed" | "partial" | "not_observed" | "passed" | "failed" | "not_evaluable" | "limited" | "blocked" | "incomplete";
      summary: string;
      evidence: string[];
    }>;
    integrity: {
      source: "deterministic_runtime_projection";
      model_self_report_used: false;
      configured_mechanism_counts_as_use: false;
      hidden_reasoning_included: false;
    };
    limitation: string;
  };
  context_lifecycle?: { contract: "hashmm.context-engine.v2"; generation: number; turns: number; compact_count: number; has_summary: boolean; checkpoint_id?: string; compacted: boolean; tool_calls: number; context_capsule?: ContextCapsule };
  handoff?: RunHandoff;
  approval_request?: ToolApprovalRequest;
  verification: { status: "passed" | "failed" | "not_evaluable"; method: string; model_self_score_used: false; failed_checks: string[]; checks: RunVerificationCheck[] };
}
export interface TraceStep { node: string; detail: string; }
export interface HookRun { hook: string; lifecycle: string; status: string; elapsed_ms: number; reason?: string; }
export interface ToolStep { tool: string; status: string; detail: string; duration_ms?: number; args?: Record<string, unknown>; hooks?: HookRun[]; receipt?: ExecutionReceipt; }
export interface FileInfo {
  filename: string;
  download_url: string;
  size?: number;
  mtime?: number;
  created_at?: number;
  lines?: number;
  resource_id?: string;
  sha256?: string;
  detected_type?: string;
  parse_state?: string;
  page_count?: number;
  readable_pages?: number;
  readable_ratio?: number;
  parser_used?: string;
  text_chars?: number;
  warnings?: string[];
}
export interface ChatResponse { answer: string; session_id: string; sources: Source[]; groundings?: GroundingLedger; run_manifest?: RunManifest; trace: TraceStep[]; elapsed_ms: number; intent: string; rewritten_query?: string | null; }
export interface Message { id?: string; role: "user" | "assistant"; content: string; sources?: Source[]; groundings?: GroundingLedger; run_manifest?: RunManifest; trace?: TraceStep[]; ts: number; status?: "streaming" | "waiting_input" | "waiting_approval" | "resolved" | "cancelled" | "interrupted" | "complete" | "done" | "error"; stop_reason?: string; feedback?: "up" | "down" | null; tokens?: { input: number; output: number }; tokens_in?: number; tokens_out?: number; files?: FileInfo[]; thinking?: string; steps?: ToolStep[]; timeline?: Array<{ kind: string; node: string; detail: string; tool?: string; status: string; elapsed_ms?: number; id: string; hooks?: HookRun[] }>; todo?: Array<{ text: string; status: string }>; suggestions?: string[]; clarify?: { question: string; options: string[] }; orchestration?: { team_id?: string; strategy?: string; status?: "running" | "stopping" | "stopped" | "done" | "failed"; retry_of?: string; members: Array<{ id: string; step?: number; role_label?: string; task?: string; status?: "pending" | "running" | "done" | "failed" | "blocked" | "waiting_input" | "stopped"; elapsed_ms?: number; preview?: string; thread_id?: string; parent_thread_id?: string; session_id?: string; scope_id?: string; session_status?: string; tool_calls?: number; allowed_tools?: string[]; session_steps?: string[]; created_at?: number; started_at?: number | null; finished_at?: number | null }> }; created_at?: number; elapsed_ms?: number; }
export interface Session {
  id: string;
  title: string;
  messages: Message[];
  created: number;
  updated_at?: number;
  content_activity_at?: number;
  metadata_updated_at?: number;
  project_id?: string;
  pinned?: boolean;
  archived?: boolean;
  has_durable_content?: boolean;
  visibility_source?: "server" | "cloud" | "local";
  revision?: number;
  sync_state?: string;
}
export interface Stats { total_chunks: number; hash_bits: number; index_size_kb: number; llm_ready: boolean; cache_size?: number; active_model?: string; modalities?: Record<string, number>; }
export interface User { id: string; username: string; display_name: string; role: "admin" | "user" | "viewer"; created_at?: number; }
export type ModelWireApi = "chat_completions" | "responses" | "anthropic_messages";
export interface ProviderSpec {
  id: string; name: string; base_url: string; wire_apis: ModelWireApi[];
  default_wire_api: ModelWireApi; auth: "bearer" | "optional" | string;
  local: boolean; base_url_required: boolean; endpoint_note: string;
  model_hints: string[];
  capabilities: {
    tools: boolean; streaming: boolean; vision: boolean; json_schema: boolean;
    model_discovery: boolean; native_state?: boolean;
    prompt_cache_telemetry?: boolean; reasoning_control?: string;
  };
}
export interface ModelConfig { id: string; name: string; provider: string; base_url: string; model_name: string; is_default: number; temperature: number; max_tokens: number; wire_api?: ModelWireApi; config?: Record<string, unknown>; config_json?: string; created_by?: string; created_at?: number; api_key?: string; }
export interface KB { id: string; name: string; description: string; allowed_roles: string; created_by: string; created_at: number; }
export interface AuditLog { id: number; user_id: string; username: string; action: string; detail: string; ip: string; ts: number; }
export interface IndexedDoc { doc_id: string; filename: string; chunks: number; modalities: string[]; }
export interface ConversationMeta {
  id: string;
  title: string;
  created_at: number;
  updated_at?: number;
  content_activity_at?: number;
  metadata_updated_at?: number;
  user_id?: string;
  pinned?: number;
  archived?: number;
  project_id?: string;
  has_durable_content?: boolean | number;
  visibility_source?: "server";
  last_activity_at?: number;
  revision?: number;
  sync_state?: string;
}
export interface InputRequest { schema: "hashmm.input-request.v1"; request_id: string; conversation_id: string; kind: "clarification"; reason: string; question: string; options: string[]; status: "waiting_input"; }
export interface StreamDoneData { sources: Source[]; groundings?: GroundingLedger; run_manifest?: RunManifest; trace: TraceStep[]; steps?: ToolStep[]; elapsed_ms: number; session_id: string; intent?: string; tokens?: { input: number; output: number }; files?: FileInfo[]; thinking?: string; suggestions?: string[]; iterations?: number; status?: Message["status"]; stop_reason?: string; input_request?: InputRequest | null; approval_request?: ToolApprovalRequest | null; }
export interface SkillData { name: string; description?: string; triggers?: string[]; prompt?: string; tools?: string[]; _path?: string; }

export const COLORS = [
  { name: "蓝", v: "#2563eb" }, { name: "青", v: "#0891b2" }, { name: "紫", v: "#7c3aed" },
  { name: "绿", v: "#059669" }, { name: "橙", v: "#d97706" }, { name: "粉", v: "#db2777" },
];
export const PROVIDERS = [
  { id: "openai", name: "OpenAI", url: "https://api.openai.com/v1" },
  { id: "azure_openai", name: "Azure OpenAI / Microsoft Foundry", url: "" },
  { id: "anthropic", name: "Claude (Anthropic)", url: "https://api.anthropic.com" },
  { id: "deepseek", name: "DeepSeek", url: "https://api.deepseek.com/v1" },
  { id: "qwen", name: "通义千问", url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { id: "zhipu", name: "智谱 GLM", url: "https://open.bigmodel.cn/api/paas/v4" },
  { id: "moonshot", name: "Moonshot", url: "https://api.moonshot.cn/v1" },
  { id: "gemini", name: "Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai/" },
  { id: "doubao", name: "火山方舟 / 豆包", url: "https://ark.cn-beijing.volces.com/api/v3" },
  { id: "baidu_qianfan", name: "百度智能云千帆", url: "" },
  { id: "tencent_hunyuan", name: "腾讯混元", url: "https://tokenhub.tencentmaas.com/v1" },
  { id: "minimax", name: "MiniMax", url: "https://api.minimaxi.com/v1" },
  { id: "mistral", name: "Mistral", url: "https://api.mistral.ai/v1" },
  { id: "xai", name: "xAI (Grok)", url: "https://api.x.ai/v1" },
  { id: "cohere", name: "Cohere", url: "https://api.cohere.ai/compatibility/v1" },
  { id: "groq", name: "Groq", url: "https://api.groq.com/openai/v1" },
  { id: "openrouter", name: "OpenRouter", url: "https://openrouter.ai/api/v1" },
  { id: "siliconflow", name: "SiliconFlow 硅基流动", url: "https://api.siliconflow.cn/v1" },
  { id: "together", name: "Together", url: "https://api.together.xyz/v1" },
  { id: "nvidia_nim", name: "NVIDIA NIM", url: "https://integrate.api.nvidia.com/v1" },
  { id: "aws_bedrock", name: "Amazon Bedrock", url: "" },
  { id: "oci_genai", name: "Oracle OCI Generative AI", url: "" },
  { id: "perplexity", name: "Perplexity Agent API", url: "https://api.perplexity.ai/v1" },
  { id: "fireworks", name: "Fireworks AI", url: "https://api.fireworks.ai/inference/v1" },
  { id: "cerebras", name: "Cerebras Inference", url: "https://api.cerebras.ai/v1" },
  { id: "github_models", name: "GitHub Models", url: "https://models.github.ai/inference" },
  { id: "sambanova", name: "SambaNova", url: "" },
  { id: "ollama", name: "Ollama", url: "http://localhost:11434/v1" },
  { id: "lmstudio", name: "LM Studio", url: "http://localhost:1234/v1" },
  { id: "vllm", name: "vLLM", url: "http://localhost:8000/v1" },
  { id: "custom", name: "自定义", url: "" },
];
