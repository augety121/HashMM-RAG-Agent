package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.cache.LocalStore
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

@Serializable
data class MobileWorkRun(
    val schema: String = "",
    val id: String = "",
    @SerialName("conversation_id") val conversationId: String = "",
    val kind: String = "workflow",
    val title: String = "",
    val status: String = "queued",
    val revision: Int = 0,
    @SerialName("project_id") val projectId: String = "",
    @SerialName("autonomy_level") val autonomyLevel: Int = 0,
    @SerialName("event_cursor") val eventCursor: Int = 0,
    @SerialName("change_cursor") val changeCursor: Long = 0,
    @SerialName("active_generation_id") val activeGenerationId: String = "",
    @SerialName("active_generation") val activeGeneration: MobileWorkGeneration? = null,
    @SerialName("artifact_revisions") val artifactRevisions: List<MobileArtifactRevision> = emptyList(),
    val control: MobileWorkControl = MobileWorkControl(),
    val presentation: MobileWorkPresentation = MobileWorkPresentation(),
    val snapshot: JsonObject = buildJsonObject { },
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("updated_at") val updatedAt: Double = 0.0,
    val events: List<MobileWorkEvent> = emptyList(),
    @SerialName("events_truncated") val eventsTruncated: Boolean = false,
)

@Serializable
data class MobileWorkPresentation(
    val schema: String = "",
    val title: String = "",
    val category: String = "工作",
    @SerialName("status_label") val statusLabel: String = "",
    val phase: String = "preparing",
    @SerialName("current_step") val currentStep: String = "",
    @SerialName("next_action") val nextAction: String = "",
    @SerialName("needs_user") val needsUser: Boolean = false,
    @SerialName("primary_action") val primaryAction: String = "open",
    val progress: MobileWorkProgress = MobileWorkProgress(),
    val deliverables: List<MobileWorkDeliverable> = emptyList(),
    val evidence: MobileWorkEvidence = MobileWorkEvidence(),
)

@Serializable
data class MobileWorkProgress(
    val mode: String = "phase",
    val completed: Int = 0,
    val total: Int = 0,
    val label: String = "",
)

@Serializable
data class MobileWorkDeliverable(
    val name: String = "",
    val type: String = "",
)

@Serializable
data class MobileWorkEvidence(
    val status: String = "not_available",
    val count: Int = 0,
    val label: String = "",
)

@Serializable
data class MobileWorkControl(
    val schema: String = "",
    @SerialName("expected_revision") val expectedRevision: Int = 0,
    @SerialName("available_actions") val availableActions: List<String> = emptyList(),
    @SerialName("side_effect_boundary") val sideEffectBoundary: String = "observe_only",
    @SerialName("latest_command") val latestCommand: MobileWorkCommand? = null,
)

@Serializable
data class MobileWorkCommand(
    val schema: String = "",
    val id: String = "",
    @SerialName("run_id") val runId: String = "",
    val action: String = "",
    @SerialName("expected_revision") val expectedRevision: Int = 0,
    val status: String = "executing",
    val result: JsonObject = buildJsonObject { },
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("finished_at") val finishedAt: Double = 0.0,
)

@Serializable
private data class MobileWorkCommandResponse(
    val ok: Boolean = false,
    val duplicate: Boolean = false,
    val error: String? = null,
    @SerialName("current_revision") val currentRevision: Int? = null,
    val command: MobileWorkCommand? = null,
    val run: MobileWorkRun? = null,
)

data class MobileWorkCommandOutcome(
    val ok: Boolean,
    val error: String? = null,
    val command: MobileWorkCommand? = null,
    val run: MobileWorkRun? = null,
)

@Serializable
data class MobileWorkOutboxItem(
    val id: String,
    val kind: String,
    @SerialName("run_id") val runId: String,
    val action: String,
    @SerialName("expected_revision") val expectedRevision: Int,
    val note: String = "",
    @SerialName("artifact_id") val artifactId: String = "",
    @SerialName("artifact_revision") val artifactRevision: Int = 0,
    val attempts: Int = 0,
    @SerialName("next_attempt_at") val nextAttemptAt: Long = 0,
    @SerialName("created_at") val createdAt: Long = System.currentTimeMillis(),
)

@Serializable
data class MobileWorkOutbox(
    val contract: String = "hashmm.mobile-work-outbox.v1",
    val items: List<MobileWorkOutboxItem> = emptyList(),
)

internal fun mobileWorkOutboxUpsert(
    outbox: MobileWorkOutbox,
    item: MobileWorkOutboxItem,
): MobileWorkOutbox = outbox.copy(
    items = (outbox.items.filterNot { it.id == item.id } + item)
        .sortedBy { it.createdAt }
        .takeLast(32),
)

internal fun mobileWorkOutboxRemove(
    outbox: MobileWorkOutbox,
    id: String,
): MobileWorkOutbox = outbox.copy(items = outbox.items.filterNot { it.id == id })

internal fun mobileWorkOutboxBackoffMs(attempts: Int): Long {
    val exponent = attempts.coerceIn(0, 8)
    return (5_000L shl exponent).coerceAtMost(15 * 60_000L)
}

@Serializable
data class MobileWorkEvent(
    val schema: String = "",
    val id: String = "",
    @SerialName("run_id") val runId: String = "",
    val seq: Int = 0,
    val type: String = "progress",
    val status: String = "",
    val summary: String = "",
    val payload: JsonObject = buildJsonObject { },
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("idempotency_key") val idempotencyKey: String = "",
    @SerialName("expected_revision") val expectedRevision: Int = 0,
    @SerialName("generation_id") val generationId: String = "",
    val envelope: MobileWorkEventEnvelope? = null,
)

@Serializable
data class MobileWorkEventEnvelope(
    val schema: String = "",
    @SerialName("event_id") val eventId: String = "",
    @SerialName("stream_id") val streamId: String = "",
    @SerialName("event_seq") val eventSeq: Long = 0,
    @SerialName("schema_version") val schemaVersion: String = "",
    val type: String = "",
    @SerialName("idempotency_key") val idempotencyKey: String = "",
    @SerialName("trace_id") val traceId: String = "",
    @SerialName("occurred_at") val occurredAt: Double = 0.0,
)

@Serializable
data class MobileWorkGeneration(
    val schema: String = "",
    val id: String = "",
    @SerialName("run_id") val runId: String = "",
    val generation: Int = 0,
    val status: String = "",
    @SerialName("manifest_hash") val manifestHash: String = "",
    val manifest: JsonObject = buildJsonObject { },
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("activated_at") val activatedAt: Double = 0.0,
)

@Serializable
data class MobileArtifactRevision(
    val schema: String = "",
    val id: String = "",
    @SerialName("artifact_id") val artifactId: String = "",
    @SerialName("run_id") val runId: String = "",
    val revision: Int = 0,
    @SerialName("content_hash") val contentHash: String = "",
    @SerialName("media_type") val mediaType: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    val locator: JsonObject = buildJsonObject { },
    val verification: String = "pending",
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
data class MobileWorkActionContext(
    val kind: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val state: String = "",
    val generation: Int = 0,
    val scopes: List<String> = emptyList(),
    @SerialName("predecessor_session_id") val predecessorSessionId: String = "",
)

@Serializable
data class MobileWorkActionItem(
    val schema: String = "",
    val id: String = "",
    @SerialName("run_id") val runId: String = "",
    @SerialName("conversation_id") val conversationId: String = "",
    val type: String = "",
    val priority: String = "normal",
    val title: String = "",
    val summary: String = "",
    @SerialName("primary_action") val primaryAction: String = "open",
    val context: MobileWorkActionContext = MobileWorkActionContext(),
    @SerialName("updated_at") val updatedAt: Double = 0.0,
)

@Serializable
data class MobileActionInbox(
    val schema: String = "",
    val items: List<MobileWorkActionItem> = emptyList(),
    val count: Int = 0,
    @SerialName("high_priority_count") val highPriorityCount: Int = 0,
)

@Serializable
data class MobileWorkFeed(
    val schema: String = "",
    val items: List<MobileWorkRun> = emptyList(),
    @SerialName("next_cursor") val nextCursor: Long = 0,
    @SerialName("high_water_cursor") val highWaterCursor: Long = 0,
    @SerialName("has_more") val hasMore: Boolean = false,
    @SerialName("action_inbox") val actionInbox: MobileActionInbox = MobileActionInbox(),
)

@Serializable
data class MobileWorkSnapshot(
    val contract: String = "hashmm.mobile-work-cache.v1",
    val cursor: Long = 0,
    @SerialName("high_water_cursor") val highWaterCursor: Long = 0,
    val runs: List<MobileWorkRun> = emptyList(),
    @SerialName("events_by_run") val eventsByRun: Map<String, List<MobileWorkEvent>> = emptyMap(),
    @SerialName("canvases_by_run") val canvasesByRun: Map<String, MobileWorkCanvas> = emptyMap(),
    @SerialName("action_inbox") val actionInbox: MobileActionInbox = MobileActionInbox(),
    val notice: String? = null,
    val error: String? = null,
)

@Serializable
private data class MobileWorkspaceEvidenceSummaryV2(
    @SerialName("evidence_count") val evidenceCount: Int = 0,
)

@Serializable
private data class MobileWorkspaceEvidenceV2(
    val summary: MobileWorkspaceEvidenceSummaryV2 = MobileWorkspaceEvidenceSummaryV2(),
)

@Serializable
private data class MobileWorkspaceArtifactV2(
    val id: String = "",
    val name: String = "",
    @SerialName("media_type") val mediaType: String = "",
    val revision: Int = 1,
    val verification: String = "pending",
    @SerialName("content_hash") val contentHash: String = "",
)

@Serializable
private data class MobileWorkspaceRunV2(
    val schema: String = "",
    val id: String = "",
    @SerialName("workspace_id") val workspaceId: String = "personal",
    @SerialName("thread_id") val threadId: String = "",
    val kind: String = "workflow",
    val state: String = "draft",
    @SerialName("legacy_status") val legacyStatus: String = "",
    val revision: Int = 1,
    @SerialName("change_cursor") val changeCursor: Long = 0,
    val title: String = "",
    @SerialName("current_step") val currentStep: String = "",
    @SerialName("next_action") val nextAction: String = "",
    @SerialName("needs_user") val needsUser: Boolean = false,
    val evidence: MobileWorkspaceEvidenceV2 = MobileWorkspaceEvidenceV2(),
    val artifacts: List<MobileWorkspaceArtifactV2> = emptyList(),
    @SerialName("available_commands") val availableCommands: List<String> = emptyList(),
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("updated_at") val updatedAt: Double = 0.0,
)

@Serializable
private data class MobileWorkspaceTodayV2(
    @SerialName("needs_user") val needsUser: List<MobileWorkspaceRunV2> = emptyList(),
    @SerialName("in_progress") val inProgress: List<MobileWorkspaceRunV2> = emptyList(),
    @SerialName("recent_results") val recentResults: List<MobileWorkspaceRunV2> = emptyList(),
)

@Serializable
private data class MobileWorkspaceSyncV2(
    @SerialName("next_cursor") val nextCursor: Long = 0,
    @SerialName("high_water_cursor") val highWaterCursor: Long = 0,
)

@Serializable
private data class MobileWorkspaceTrustV2(
    @SerialName("owner_isolation") val ownerIsolation: Boolean = false,
    @SerialName("model_prose_is_execution_evidence") val modelProseIsExecutionEvidence: Boolean = true,
)

@Serializable
private data class MobileWorkspaceSnapshotV2(
    val schema: String = "",
    val today: MobileWorkspaceTodayV2 = MobileWorkspaceTodayV2(),
    val runs: List<MobileWorkspaceRunV2> = emptyList(),
    val sync: MobileWorkspaceSyncV2 = MobileWorkspaceSyncV2(),
    val trust: MobileWorkspaceTrustV2 = MobileWorkspaceTrustV2(),
)

internal fun canonicalWorkStatus(state: String, legacy: String = ""): String = when (state) {
    "draft", "planned", "ready" -> "queued"
    "running", "change_requested" -> "running"
    "waiting_user" -> "waiting_input"
    "waiting_approval" -> "waiting_approval"
    "blocked" -> "blocked"
    "review" -> "delivered"
    "accepted", "completed" -> "completed"
    "failed" -> "failed"
    "cancelled" -> "cancelled"
    "interrupted" -> "interrupted"
    "observed" -> "observed"
    else -> legacy.ifBlank { "blocked" }
}

private fun canonicalWorkLabel(state: String): String = when (state) {
    "draft" -> "草稿"
    "planned" -> "已规划"
    "ready" -> "待开始"
    "running" -> "进行中"
    "waiting_user" -> "需要补充"
    "waiting_approval" -> "等待确认"
    "blocked" -> "暂时受阻"
    "review" -> "等待验收"
    "accepted" -> "已验收"
    "change_requested" -> "修改中"
    "completed" -> "已完成"
    "failed" -> "未完成"
    "cancelled" -> "已取消"
    "interrupted" -> "已中断"
    "observed" -> "历史记录"
    else -> "状态更新"
}

private fun MobileWorkspaceRunV2.toLegacyProjection(): MobileWorkRun {
    val status = canonicalWorkStatus(state, legacyStatus)
    val deliverables = artifacts.map {
        MobileWorkDeliverable(name = it.name.ifBlank { it.id }, type = it.mediaType)
    }
    val revisions = artifacts.map {
        MobileArtifactRevision(
            id = "${it.id}:${it.revision}",
            artifactId = it.id,
            runId = id,
            revision = it.revision,
            contentHash = it.contentHash,
            mediaType = it.mediaType,
            verification = it.verification,
            createdAt = updatedAt,
        )
    }
    return MobileWorkRun(
        schema = "hashmm.work-run.v1",
        id = id,
        conversationId = threadId,
        kind = kind,
        title = title,
        status = status,
        revision = revision,
        projectId = workspaceId.takeUnless { it == "personal" }.orEmpty(),
        changeCursor = changeCursor,
        artifactRevisions = revisions,
        control = MobileWorkControl(
            schema = "hashmm.work-control.v1",
            expectedRevision = revision,
            availableActions = availableCommands,
        ),
        presentation = MobileWorkPresentation(
            schema = "hashmm.work-presentation.v1",
            title = title,
            statusLabel = canonicalWorkLabel(state),
            phase = when {
                state in setOf("accepted", "completed", "observed", "cancelled") -> "finished"
                needsUser -> "needs_user"
                else -> "working"
            },
            currentStep = currentStep,
            nextAction = nextAction,
            needsUser = needsUser,
            primaryAction = "open",
            progress = MobileWorkProgress(label = nextAction),
            deliverables = deliverables,
            evidence = MobileWorkEvidence(
                status = if (evidence.summary.evidenceCount > 0) "available" else "not_available",
                count = evidence.summary.evidenceCount,
                label = if (evidence.summary.evidenceCount > 0) "${evidence.summary.evidenceCount} 条依据" else "尚无完成依据",
            ),
        ),
        createdAt = createdAt,
        updatedAt = updatedAt,
    )
}

private fun MobileWorkspaceRunV2.toLegacyActionItem(): MobileWorkActionItem {
    val run = toLegacyProjection()
    return MobileWorkActionItem(
        schema = "hashmm.action-item.v1",
        id = "action:${run.id}",
        runId = run.id,
        conversationId = run.conversationId,
        type = when (run.status) {
            "waiting_approval" -> "approval"
            "waiting_input" -> "question"
            "delivered" -> "delivery"
            "failed" -> "failure"
            "interrupted" -> "interruption"
            else -> "blocker"
        },
        priority = if (
            run.status in setOf(
                "waiting_approval", "waiting_input", "blocked", "failed",
            )
        ) "high" else "normal",
        title = run.presentation.title,
        summary = run.presentation.currentStep,
        primaryAction = run.presentation.primaryAction,
        updatedAt = run.updatedAt,
    )
}

private fun MobileWorkspaceSnapshotV2.isTrustedWorkspace(): Boolean =
    schema == "hashmm.workspace.v2" &&
        trust.ownerIsolation &&
        !trust.modelProseIsExecutionEvidence

private fun MobileWorkspaceSnapshotV2.toLegacyFeed(): MobileWorkFeed {
    val actionItems = today.needsUser.map { it.toLegacyActionItem() }
    return MobileWorkFeed(
        schema = "hashmm.work-feed.v1",
        items = runs.map { it.toLegacyProjection() }.distinctBy { it.id },
        nextCursor = sync.nextCursor,
        highWaterCursor = sync.highWaterCursor,
        hasMore = false,
        actionInbox = MobileActionInbox(
            schema = "hashmm.action-inbox.v1",
            items = actionItems,
            count = actionItems.size,
            highPriorityCount = actionItems.count { it.priority == "high" },
        ),
    )
}

internal fun parseMobileWorkspaceFeed(raw: String): MobileWorkFeed? {
    val workspace = runCatching {
        workJson.decodeFromString<MobileWorkspaceSnapshotV2>(raw)
    }.getOrNull() ?: return null
    return workspace.takeIf { it.isTrustedWorkspace() }?.toLegacyFeed()
}

@Serializable
data class MobileWorkIdentity(
    @SerialName("work_id") val workId: String = "",
    @SerialName("project_id") val projectId: String = "",
    @SerialName("conversation_id") val conversationId: String = "",
)

@Serializable
data class MobileWorkContract(
    val goal: String = "",
    val constraints: List<String> = emptyList(),
    @SerialName("completion_criteria") val completionCriteria: List<MobileWorkCriterion> = emptyList(),
)

@Serializable
data class MobileWorkCriterion(
    val id: String = "",
    val label: String = "",
    val required: Boolean = true,
)

@Serializable
data class MobileExecutionPlacement(
    val kind: String = "waiting_device",
    @SerialName("target_id") val targetId: String = "",
    val label: String = "等待电脑上线",
    val state: String = "waiting",
    val lease: MobileExecutionLease? = null,
)

@Serializable
data class MobileExecutionLease(
    val id: String = "",
    val state: String = "",
    @SerialName("holder_type") val holderType: String = "",
    @SerialName("holder_id") val holderId: String = "",
    val generation: Int = 0,
    @SerialName("expires_at") val expiresAt: Double = 0.0,
)

@Serializable
data class MobileExecutionDevice(
    @SerialName("device_id") val deviceId: String = "",
    val name: String = "电脑",
    val runner: String = "desktop",
    val version: String = "",
    val online: Boolean = false,
    @SerialName("last_seen") val lastSeen: Double = 0.0,
)

@Serializable
private data class MobileExecutionDevicesResponse(
    val schema: String = "",
    val items: List<MobileExecutionDevice> = emptyList(),
    @SerialName("online_count") val onlineCount: Int = 0,
)

@Serializable
private data class MobilePlacementResponse(
    val ok: Boolean = false,
    val error: String? = null,
    @SerialName("current_revision") val currentRevision: Int? = null,
)

data class MobilePlacementOutcome(
    val ok: Boolean,
    val error: String? = null,
)

@Serializable
data class MobileCapabilityPlan(
    val selected: String = "manual",
    val label: String = "需要你手动完成一步",
    val reason: String = "",
    @SerialName("requires_confirmation") val requiresConfirmation: Boolean = true,
)

@Serializable
data class MobileWorkTwinSummary(
    val goals: Int = 0,
    val criteria: Int = 0,
    val tasks: Int = 0,
    val evidence: Int = 0,
    val artifacts: Int = 0,
    val stale: Int = 0,
)

@Serializable
data class MobileWorkTwin(
    val schema: String = "",
    val summary: MobileWorkTwinSummary = MobileWorkTwinSummary(),
)

@Serializable
data class MobileRecoveryArea(
    val available: Boolean = false,
    @SerialName("checkpoint_count") val checkpointCount: Int = 0,
    @SerialName("previous_versions") val previousVersions: Int = 0,
)

@Serializable
data class MobileRecoveryCenter(
    val schema: String = "",
    val conversation: MobileRecoveryArea = MobileRecoveryArea(),
    val files: MobileRecoveryArea = MobileRecoveryArea(),
    val execution: MobileRecoveryArea = MobileRecoveryArea(),
    @SerialName("automatic_rollback_available") val automaticRollbackAvailable: Boolean = false,
)

@Serializable
data class MobileWorkIsolation(
    val status: String = "not_proven",
    val mode: String = "",
    @SerialName("integrator_only_merge") val integratorOnlyMerge: Boolean = false,
    @SerialName("verifier_read_only") val verifierReadOnly: Boolean = false,
)

@Serializable
data class MobileWorkCollaboration(
    val schema: String = "",
    val strategy: String = "single_agent",
    @SerialName("user_summary") val userSummary: String = "",
    @SerialName("write_isolation") val writeIsolation: MobileWorkIsolation = MobileWorkIsolation(),
)

@Serializable
data class MobileAutonomyProfile(
    val schema: String = "",
    @SerialName("released_level") val releasedLevel: Int = 0,
    val label: String = "回答与建议",
    @SerialName("evaluation_receipt_valid") val evaluationReceiptValid: Boolean = false,
)

@Serializable
data class MobileWorkflowCandidate(
    val id: String = "",
    val revision: Int = 0,
    val status: String = "evidence_required",
    @SerialName("required_next_step") val requiredNextStep: String = "",
    @SerialName("can_publish") val canPublish: Boolean = false,
)

@Serializable
private data class MobileWorkflowMutationResponse(
    val ok: Boolean = false,
    val error: String? = null,
)

data class MobileWorkflowOutcome(
    val ok: Boolean,
    val error: String? = null,
)

@Serializable
data class MobileArtifactAnnotation(
    val id: String = "",
    @SerialName("artifact_id") val artifactId: String = "",
    @SerialName("artifact_revision") val artifactRevision: Int = 0,
    val note: String = "",
    val status: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
data class MobileWorkProjection(
    val schema: String = "",
    val identity: MobileWorkIdentity = MobileWorkIdentity(),
    val contract: MobileWorkContract = MobileWorkContract(),
    val placement: MobileExecutionPlacement = MobileExecutionPlacement(),
    @SerialName("capability_plan") val capabilityPlan: MobileCapabilityPlan = MobileCapabilityPlan(),
    @SerialName("work_twin") val workTwin: MobileWorkTwin = MobileWorkTwin(),
    val annotations: List<MobileArtifactAnnotation> = emptyList(),
    val recovery: MobileRecoveryCenter = MobileRecoveryCenter(),
    val collaboration: MobileWorkCollaboration = MobileWorkCollaboration(),
    val autonomy: MobileAutonomyProfile = MobileAutonomyProfile(),
    @SerialName("workflow_candidate") val workflowCandidate: MobileWorkflowCandidate = MobileWorkflowCandidate(),
)

@Serializable
data class MobileOperatingProjection(
    val schema: String = "",
    @SerialName("method_label") val methodLabel: String = "自动安排",
    @SerialName("route_state") val routeState: String = "setup_required",
    @SerialName("route_label") val routeLabel: String = "运行方式待确认",
    val reason: String = "",
    @SerialName("requires_confirmation") val requiresConfirmation: Boolean = false,
    @SerialName("can_resume") val canResume: Boolean = false,
    @SerialName("evidence_required") val evidenceRequired: Boolean = false,
    val revision: String = "",
)

@Serializable
data class MobileWorkCanvas(
    val schema: String = "",
    @SerialName("run_id") val runId: String = "",
    @SerialName("conversation_id") val conversationId: String = "",
    val overview: MobileWorkCanvasOverview = MobileWorkCanvasOverview(),
    val process: MobileWorkCanvasProcess = MobileWorkCanvasProcess(),
    val evidence: MobileWorkCanvasEvidence = MobileWorkCanvasEvidence(),
    val results: List<MobileWorkCanvasResult> = emptyList(),
    @SerialName("completion_receipt") val completionReceipt: MobileCompletionReceipt = MobileCompletionReceipt(),
    @SerialName("next_actions") val nextActions: MobileNextActions = MobileNextActions(),
    val learning: MobileSkillLearning = MobileSkillLearning(),
    @SerialName("change_impact") val changeImpact: MobileChangeImpact = MobileChangeImpact(),
    val assurance: MobileWorkAssurance = MobileWorkAssurance(),
    val operating: MobileOperatingProjection = MobileOperatingProjection(),
    val product: MobileWorkProjection? = null,
    val sync: MobileWorkCanvasSync = MobileWorkCanvasSync(),
    val integrity: MobileWorkCanvasIntegrity = MobileWorkCanvasIntegrity(),
    @SerialName("latest_decision") val latestDecision: MobileWorkDecision? = null,
)

data class MobileWorkCanvasLookup(
    val canvas: MobileWorkCanvas? = null,
    val error: String? = null,
)

@Serializable
data class MobileWorkAssuranceSummary(
    val state: String = "not_verified",
    val headline: String = "",
    val detail: String = "",
    @SerialName("checks_observed") val checksObserved: Int = 0,
    @SerialName("checks_total") val checksTotal: Int = 0,
)

@Serializable
data class MobileWorkAssuranceRecovery(
    val status: String = "unavailable",
    @SerialName("can_resume") val canResume: Boolean = false,
    @SerialName("checkpoint_count") val checkpointCount: Int = 0,
)

@Serializable
data class MobileWorkAssuranceEvidence(
    val status: String = "incomplete",
    val required: Int = 0,
    val passed: Int = 0,
    val failed: Int = 0,
)

@Serializable
data class MobileWorkAssuranceBlocker(
    val code: String = "",
    val area: String = "",
)

@Serializable
data class MobileWorkAssuranceDelivery(
    val status: String = "not_verified",
    @SerialName("can_deliver") val canDeliver: Boolean = false,
    val blockers: List<MobileWorkAssuranceBlocker> = emptyList(),
)

@Serializable
data class MobileWorkAssurance(
    val schema: String = "",
    @SerialName("user_summary") val userSummary: MobileWorkAssuranceSummary = MobileWorkAssuranceSummary(),
    val recovery: MobileWorkAssuranceRecovery = MobileWorkAssuranceRecovery(),
    val evidence: MobileWorkAssuranceEvidence = MobileWorkAssuranceEvidence(),
    val delivery: MobileWorkAssuranceDelivery = MobileWorkAssuranceDelivery(),
)

@Serializable
data class MobileSkillVersion(
    val id: String = "",
    val name: String = "",
    val scope: String = "",
    @SerialName("version_ref") val versionRef: String = "",
    @SerialName("evolution_id") val evolutionId: String = "",
    val status: String = "",
)

@Serializable
data class MobileSkillLearningGovernance(
    @SerialName("records_exact_version") val recordsExactVersion: Boolean = false,
    @SerialName("prompt_body_exposed") val promptBodyExposed: Boolean = false,
    @SerialName("automatic_promotion_allowed") val automaticPromotionAllowed: Boolean = false,
    @SerialName("owner_bound_feedback_required") val ownerBoundFeedbackRequired: Boolean = false,
    @SerialName("paired_replay_required_before_promotion") val pairedReplayRequiredBeforePromotion: Boolean = false,
    @SerialName("safety_regression_blocks_promotion") val safetyRegressionBlocksPromotion: Boolean = false,
)

@Serializable
data class MobileSkillLearning(
    val schema: String = "",
    @SerialName("used_versions") val usedVersions: List<MobileSkillVersion> = emptyList(),
    val governance: MobileSkillLearningGovernance = MobileSkillLearningGovernance(),
    val limitation: String = "",
)

@Serializable
data class MobileChangeImpact(
    val schema: String = "",
    val requested: Boolean = false,
    val reason: String = "",
    @SerialName("impacted_result_ids") val impactedResultIds: List<String> = emptyList(),
    @SerialName("impacted_stage_ids") val impactedStageIds: List<String> = emptyList(),
    @SerialName("next_action") val nextAction: String = "",
)

@Serializable
data class MobileWorkCanvasOverview(
    val title: String = "",
    val category: String = "",
    @SerialName("status_label") val statusLabel: String = "",
    val phase: String = "",
    val goal: String = "",
    @SerialName("current_step") val currentStep: String = "",
    @SerialName("next_action") val nextAction: String = "",
    val progress: MobileWorkProgress = MobileWorkProgress(),
    @SerialName("needs_user") val needsUser: Boolean = false,
    @SerialName("primary_action") val primaryAction: String = "",
)

@Serializable
data class MobileWorkCanvasStage(
    val id: String = "",
    val order: Int = 0,
    val label: String = "",
    val status: String = "pending",
)

@Serializable
data class MobileWorkCanvasCheckpoint(
    val id: String = "",
    val label: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
    val kind: String = "",
    @SerialName("can_resume") val canResume: Boolean = false,
)

@Serializable
data class MobileWorkCanvasEvent(
    val id: String = "",
    val type: String = "",
    val summary: String = "",
    val status: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
data class MobileWorkCanvasBranch(
    val id: String = "",
    val label: String = "",
    val status: String = "",
)

@Serializable
data class MobileWorkCanvasProcess(
    val stages: List<MobileWorkCanvasStage> = emptyList(),
    val branches: List<MobileWorkCanvasBranch> = emptyList(),
    val checkpoints: List<MobileWorkCanvasCheckpoint> = emptyList(),
    @SerialName("event_count") val eventCount: Int = 0,
    @SerialName("latest_events") val latestEvents: List<MobileWorkCanvasEvent> = emptyList(),
)

@Serializable
data class MobileWorkEvidenceSource(
    val id: String = "",
    val kind: String = "",
    val label: String = "",
    val status: String = "",
    val trust: String = "",
    val revision: Int = 1,
)

@Serializable
data class MobileWorkEvidenceCheck(
    val id: String = "",
    val label: String = "",
    val status: String = "",
    val detail: String = "",
)

@Serializable
data class MobileExecutionReceipt(
    val id: String = "",
    val tool: String = "",
    val status: String = "",
    val success: Boolean = false,
    @SerialName("side_effect") val sideEffect: String = "",
    val reversible: Boolean = false,
    val valid: Boolean = false,
)

@Serializable
data class MobileWorkEvidenceSummary(
    val sources: Int = 0,
    val checks: Int = 0,
    val receipts: Int = 0,
    @SerialName("invalid_receipts") val invalidReceipts: Int = 0,
    @SerialName("stale_nodes") val staleNodes: Int = 0,
)

@Serializable
data class MobileWorkInvalidation(
    val strategy: String = "",
    @SerialName("stale_node_ids") val staleNodeIds: List<String> = emptyList(),
)

@Serializable
data class MobileWorkCanvasEvidence(
    val status: String = "",
    val sources: List<MobileWorkEvidenceSource> = emptyList(),
    val checks: List<MobileWorkEvidenceCheck> = emptyList(),
    @SerialName("execution_receipts") val executionReceipts: List<MobileExecutionReceipt> = emptyList(),
    val summary: MobileWorkEvidenceSummary = MobileWorkEvidenceSummary(),
    val invalidation: MobileWorkInvalidation = MobileWorkInvalidation(),
)

@Serializable
data class MobileWorkCanvasResult(
    val schema: String = "",
    val id: String = "",
    val name: String = "",
    val kind: String = "file",
    val version: Int = 1,
    @SerialName("version_ref") val versionRef: String = "",
    val verification: String = "reported",
    val size: Long = 0,
    @SerialName("download_url") val downloadUrl: String = "",
    val actions: List<String> = emptyList(),
    @SerialName("evidence_node_id") val evidenceNodeId: String = "",
    @SerialName("updated_at") val updatedAt: Double = 0.0,
)

@Serializable
data class MobileCompletionSummary(
    val results: Int = 0,
    val sources: Int = 0,
    val checks: Int = 0,
    @SerialName("side_effects") val sideEffects: Int = 0,
    @SerialName("irreversible_side_effects") val irreversibleSideEffects: Int = 0,
)

@Serializable
data class MobileCompletionVerification(
    val status: String = "",
    val criteria: List<JsonObject> = emptyList(),
    @SerialName("next_action") val nextAction: String = "",
)

@Serializable
data class MobileCompletionReview(
    val status: String = "pending",
    val authority: String = "",
    @SerialName("decided_at") val decidedAt: Double = 0.0,
)

@Serializable
data class MobileCompletionSideEffect(
    val tool: String = "",
    val status: String = "",
    @SerialName("class") val effectClass: String = "",
    val reversible: Boolean = false,
)

@Serializable
data class MobileCompletionRecovery(
    val checkpoints: Int = 0,
    @SerialName("can_resume") val canResume: Boolean = false,
    @SerialName("can_retry") val canRetry: Boolean = false,
    @SerialName("automatic_rollback_available") val automaticRollbackAvailable: Boolean = false,
)

@Serializable
data class MobileCompletionReceipt(
    val schema: String = "",
    @SerialName("receipt_id") val receiptId: String = "",
    @SerialName("run_id") val runId: String = "",
    val status: String = "not_ready",
    @SerialName("can_claim_complete") val canClaimComplete: Boolean = false,
    @SerialName("can_claim_verified") val canClaimVerified: Boolean = false,
    val summary: MobileCompletionSummary = MobileCompletionSummary(),
    val verification: MobileCompletionVerification = MobileCompletionVerification(),
    @SerialName("user_review") val userReview: MobileCompletionReview = MobileCompletionReview(),
    @SerialName("side_effects") val sideEffects: List<MobileCompletionSideEffect> = emptyList(),
    val limitations: List<String> = emptyList(),
    val recovery: MobileCompletionRecovery = MobileCompletionRecovery(),
    @SerialName("completed_at") val completedAt: Double = 0.0,
)

@Serializable
data class MobileGovernedNextAction(
    val id: String = "",
    val kind: String = "",
    val label: String = "",
    val reason: String = "",
    val risk: String = "low",
    @SerialName("within_scope") val withinScope: Boolean = false,
    @SerialName("requires_confirmation") val requiresConfirmation: Boolean = true,
    @SerialName("control_action") val controlAction: String = "",
    @SerialName("auto_execute") val autoExecute: Boolean = false,
)

@Serializable
data class MobileNextActionGovernance(
    @SerialName("approval_mode") val approvalMode: String = "",
    @SerialName("network_mode") val networkMode: String = "",
    @SerialName("high_risk_observations") val highRiskObservations: Int = 0,
    @SerialName("auto_execution_enabled") val autoExecutionEnabled: Boolean = false,
    @SerialName("widens_scope") val widensScope: Boolean = false,
)

@Serializable
data class MobileNextActions(
    val schema: String = "",
    val items: List<MobileGovernedNextAction> = emptyList(),
    val governance: MobileNextActionGovernance = MobileNextActionGovernance(),
    val limitation: String = "",
)

@Serializable
data class MobileWorkCanvasSync(
    val revision: Int = 0,
    @SerialName("event_cursor") val eventCursor: Int = 0,
    @SerialName("change_cursor") val changeCursor: Long = 0,
    val etag: String = "",
    @SerialName("updated_at") val updatedAt: Double = 0.0,
    @SerialName("active_generation_id") val activeGenerationId: String = "",
    @SerialName("generation_hash") val generationHash: String = "",
    @SerialName("invalidated_result_ids") val invalidatedResultIds: List<String> = emptyList(),
)

@Serializable
data class MobileWorkCanvasIntegrity(
    @SerialName("projection_only") val projectionOnly: Boolean = true,
    @SerialName("auto_executes") val autoExecutes: Boolean = false,
    @SerialName("widens_scope") val widensScope: Boolean = false,
    @SerialName("model_prose_is_evidence") val modelProseIsEvidence: Boolean = false,
    @SerialName("owner_check_required_by_api") val ownerCheckRequiredByApi: Boolean = true,
)

@Serializable
data class MobileWorkDecision(
    val schema: String = "",
    val id: String = "",
    @SerialName("run_id") val runId: String = "",
    val action: String = "",
    @SerialName("expected_revision") val expectedRevision: Int = 0,
    val status: String = "",
    val note: String = "",
    val result: JsonObject = buildJsonObject { },
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
private data class MobileWorkDecisionResponse(
    val ok: Boolean = false,
    val duplicate: Boolean = false,
    val error: String? = null,
    val reason: String? = null,
    @SerialName("current_revision") val currentRevision: Int? = null,
    val decision: MobileWorkDecision? = null,
    val run: MobileWorkRun? = null,
)

data class MobileWorkDecisionOutcome(
    val ok: Boolean,
    val error: String? = null,
    val decision: MobileWorkDecision? = null,
    val run: MobileWorkRun? = null,
)

@Serializable
private data class MobileWorkAnnotationResponse(
    val ok: Boolean = false,
    val duplicate: Boolean = false,
    val error: String? = null,
    @SerialName("current_revision") val currentRevision: Int? = null,
    val annotation: MobileArtifactAnnotation? = null,
)

data class MobileWorkAnnotationOutcome(
    val ok: Boolean,
    val error: String? = null,
    val annotation: MobileArtifactAnnotation? = null,
)

private val workJson = Json { ignoreUnknownKeys = true; encodeDefaults = true }

fun parseMobileWorkFeed(raw: String): MobileWorkFeed? =
    runCatching { workJson.decodeFromString<MobileWorkFeed>(raw) }.getOrNull()
        ?.takeIf { it.schema == "hashmm.work-feed.v1" }

fun parseMobileWorkRun(raw: String): MobileWorkRun? =
    runCatching { workJson.decodeFromString<MobileWorkRun>(raw) }.getOrNull()
        ?.takeIf { it.schema == "hashmm.work-run.v1" && it.id.isNotBlank() }

fun parseMobileWorkCanvas(raw: String): MobileWorkCanvas? =
    runCatching { workJson.decodeFromString<MobileWorkCanvas>(raw) }.getOrNull()
        ?.takeIf {
            it.schema == "hashmm.work-canvas.v1" &&
                it.runId.isNotBlank() &&
                it.integrity.projectionOnly &&
                !it.integrity.autoExecutes &&
                !it.integrity.widensScope &&
                !it.learning.governance.automaticPromotionAllowed &&
                !it.learning.governance.promptBodyExposed
        }

internal fun workMemoryOwnerChanged(cachedOwnerId: String?, requestedOwnerId: String): Boolean =
    cachedOwnerId != requestedOwnerId

internal fun mobileControlError(code: String?, currentRevision: Int?): String = when (code) {
    "revision_conflict" -> "任务状态已经变化${currentRevision?.let { "（当前 r$it）" }.orEmpty()}，请刷新后再操作"
    "unsupported" -> "当前任务状态不支持这个操作"
    "already_claimed_or_finished" -> "桌面端已经领取或完成任务，无法再撤回"
    "executor_state_changed", "executor_rejected" -> "执行器状态已经变化，操作没有执行"
    "command_id_conflict" -> "控制请求标识冲突，操作没有执行"
    "executor_error" -> "任务控制执行失败"
    else -> "任务控制失败"
}

internal fun mobileDecisionError(code: String?, reason: String?, currentRevision: Int?): String = when (code) {
    "revision_conflict" -> "工作状态已经变化${currentRevision?.let { "（当前 r$it）" }.orEmpty()}，请刷新后再验收"
    "unsupported" -> "当前工作状态不支持这个验收操作"
    "note_required" -> "请写明需要修改的内容"
    "verification_blocked" -> reason?.takeIf { it.isNotBlank() } ?: "依据已变化，重新核验后才能验收"
    "decision_id_conflict" -> "验收请求标识冲突，操作没有执行"
    else -> reason?.takeIf { it.isNotBlank() } ?: "验收状态保存失败"
}

/**
 * Offline-first incremental projection of the server's unified work runtime.
 * The server is authoritative. App stores only redacted run projections in the
 * existing per-account encrypted cache and advances a monotonic cursor.
 */
@Singleton
class WorkRuntimeRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
    private val local: LocalStore,
) {
    private val http = SharedHttp.base.newBuilder().callTimeout(12, TimeUnit.SECONDS).build()
    private val streamHttp = SharedHttp.base.newBuilder()
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val mutex = Mutex()
    private val outboxMutex = Mutex()
    @Volatile private var memory: MobileWorkSnapshot? = null
    @Volatile private var memoryAtMs: Long = 0
    @Volatile private var memoryOwnerId: String? = null

    /** Receive owner-scoped work deltas without putting a bearer token in the
     * URL. The caller owns reconnect policy; ordinary ETag/cursor reads remain
     * the repair path after sleep, network changes or proxy buffering. */
    suspend fun streamChanges(onFeed: (MobileWorkFeed) -> Unit): Boolean = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        val cursor = memory?.cursor ?: 0L
        val request = Request.Builder()
            .url("$base/api/v2/workspaces/personal/stream?after_cursor=$cursor")
            .header("Authorization", "Bearer $token")
            .header("Accept", "text/event-stream")
            .get()
            .build()
        try {
            streamHttp.newCall(request).execute().use { response ->
                if (response.code == 404) {
                    return@withContext streamLegacyChanges(base, token, cursor, onFeed)
                }
                if (!response.isSuccessful) return@withContext false
                val reader = response.body?.charStream()?.buffered() ?: return@withContext false
                val data = StringBuilder()
                while (true) {
                    currentCoroutineContext().ensureActive()
                    val line = reader.readLine() ?: break
                    if (line.isBlank()) {
                        if (data.isNotEmpty()) {
                            val feed = parseMobileWorkspaceFeed(data.toString())
                            if (feed == null) {
                                return@withContext false
                            }
                            onFeed(feed)
                            data.setLength(0)
                        }
                    } else if (line.startsWith("data:")) {
                        if (data.isNotEmpty()) data.append('\n')
                        data.append(line.removePrefix("data:").trimStart())
                    }
                }
                true
            }
        } catch (_: Exception) {
            false
        }
    }

    private suspend fun streamLegacyChanges(
        base: String,
        token: String,
        cursor: Long,
        onFeed: (MobileWorkFeed) -> Unit,
    ): Boolean {
        val request = Request.Builder()
            .url("$base/api/work-runs/stream?after_cursor=$cursor")
            .header("Authorization", "Bearer $token")
            .header("Accept", "text/event-stream")
            .get()
            .build()
        return try {
            streamHttp.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return false
                val reader = response.body?.charStream()?.buffered() ?: return false
                val data = StringBuilder()
                while (true) {
                    currentCoroutineContext().ensureActive()
                    val line = reader.readLine() ?: break
                    if (line.isBlank()) {
                        if (data.isNotEmpty()) {
                            parseMobileWorkFeed(data.toString())?.let(onFeed)
                            data.setLength(0)
                        }
                    } else if (line.startsWith("data:")) {
                        if (data.isNotEmpty()) data.append('\n')
                        data.append(line.removePrefix("data:").trimStart())
                    }
                }
                true
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun fetch(force: Boolean = false): MobileWorkSnapshot {
        val uid = auth.currentUserId() ?: return MobileWorkSnapshot(error = "请先登录")
        return mutex.withLock {
            ensureMemoryOwner(uid)
            val now = System.currentTimeMillis()
            memory?.takeIf { !force && now - memoryAtMs < MEMORY_TTL_MS }
                ?.let { return@withLock it }
            val persisted = local.getWorkRuntimeSnapshot(uid)?.let {
                runCatching { workJson.decodeFromString<MobileWorkSnapshot>(it) }.getOrNull()
            }?.takeIf { it.contract == "hashmm.mobile-work-cache.v1" }
            val remembered = memory ?: persisted ?: MobileWorkSnapshot()
            // A forced feed refresh restarts the incremental cursor, but keeps
            // owner-scoped event/canvas caches. Workspace ETags independently
            // decide whether those projections changed.
            val baseState = if (force) {
                remembered.copy(cursor = 0, highWaterCursor = 0, runs = emptyList())
            } else remembered
            val pendingOutbox = reconcileOutbox(uid)
            val fresh = fetchRemote(uid, baseState)
            if (fresh.error == null) {
                val saved = fresh.copy(
                    notice = if (pendingOutbox > 0) {
                        "$pendingOutbox 项操作正在等待服务器确认"
                    } else null,
                    error = null,
                )
                local.putWorkRuntimeSnapshot(uid, workJson.encodeToString(saved))
                memory = saved
                memoryAtMs = System.currentTimeMillis()
                saved
            } else if (persisted != null) {
                persisted.copy(notice = "服务器暂时不可达，显示最近一次同步的工作状态", error = null)
                    .also { memory = it; memoryAtMs = System.currentTimeMillis() }
            } else fresh
        }
    }

    /** Incrementally load one run's bounded event history for the native detail page. */
    suspend fun detail(runId: String, force: Boolean = false): MobileWorkRun? {
        val cleanId = runId.trim().take(96)
        val uid = auth.currentUserId() ?: return null
        if (cleanId.isBlank()) return null
        return mutex.withLock {
            ensureMemoryOwner(uid)
            val persisted = local.getWorkRuntimeSnapshot(uid)?.let {
                runCatching { workJson.decodeFromString<MobileWorkSnapshot>(it) }.getOrNull()
            }?.takeIf { it.contract == "hashmm.mobile-work-cache.v1" }
            val baseState = memory ?: persisted ?: MobileWorkSnapshot()
            val cachedRun = baseState.runs.firstOrNull { it.id == cleanId }
            val cachedEvents = if (force) emptyList() else baseState.eventsByRun[cleanId].orEmpty()
            val detail = fetchRemoteDetail(uid, cleanId, cachedRun, cachedEvents, force)
                ?: return@withLock cachedRun?.copy(events = cachedEvents)
            val cleanRun = detail.copy(events = emptyList())
            val runs = (baseState.runs.filterNot { it.id == cleanId } + cleanRun)
                .sortedByDescending { it.updatedAt }.take(MAX_RUNS)
            val events = (baseState.eventsByRun + (cleanId to detail.events.takeLast(MAX_EVENTS)))
                .filterKeys { key -> runs.any { it.id == key } }
            val saved = baseState.copy(runs = runs, eventsByRun = events, notice = null, error = null)
            local.putWorkRuntimeSnapshot(uid, workJson.encodeToString(saved))
            memory = saved
            memoryAtMs = System.currentTimeMillis()
            detail
        }
    }

    /** Load the single user-facing work canvas. The payload is owner checked
     * by the server and cached per account with a private ETag. A 304 response
     * reuses the encrypted local projection without touching Supabase. */
    suspend fun workspace(runId: String, force: Boolean = false): MobileWorkCanvas? =
        workspaceResult(runId, force).canvas

    suspend fun workspaceResult(runId: String, force: Boolean = false): MobileWorkCanvasLookup {
        val cleanId = runId.trim().take(96)
        val uid = auth.currentUserId()
            ?: return MobileWorkCanvasLookup(error = "登录状态不可用，请重新登录")
        if (cleanId.isBlank()) return MobileWorkCanvasLookup(error = "工作编号无效")
        return mutex.withLock {
            ensureMemoryOwner(uid)
            val persisted = local.getWorkRuntimeSnapshot(uid)?.let {
                runCatching { workJson.decodeFromString<MobileWorkSnapshot>(it) }.getOrNull()
            }?.takeIf { it.contract == "hashmm.mobile-work-cache.v1" }
            val state = memory ?: persisted ?: MobileWorkSnapshot()
            val cached = state.canvasesByRun[cleanId]
            val remote = fetchRemoteWorkspace(uid, cleanId, cached, force)
            val fresh = remote.canvas
            val selected = fresh ?: cached
                ?: return@withLock MobileWorkCanvasLookup(error = remote.error ?: "这项工作当前不可用")
            if (fresh != null) {
                val saved = state.copy(
                    canvasesByRun = state.canvasesByRun + (cleanId to fresh),
                    notice = null,
                    error = null,
                )
                local.putWorkRuntimeSnapshot(uid, workJson.encodeToString(saved))
                memory = saved
                memoryAtMs = System.currentTimeMillis()
            }
            MobileWorkCanvasLookup(canvas = selected)
        }
    }

    /** Read owner-scoped online devices. Local discovery is not execution
     * evidence and is therefore never substituted for the server response. */
    suspend fun executionDevices(): List<MobileExecutionDevice> =
        withContext(Dispatchers.IO) {
            val base = base()
            val token = auth.currentToken()
            if (base.isBlank() || token.isNullOrBlank()) return@withContext emptyList()
            try {
                val request = Request.Builder()
                    .url("$base/api/work-runs/devices")
                    .header("Authorization", "Bearer $token")
                    .get()
                    .build()
                http.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) return@withContext emptyList()
                    workJson.decodeFromString<MobileExecutionDevicesResponse>(
                        response.body?.string().orEmpty(),
                    ).items.filter { it.online && it.deviceId.isNotBlank() }
                }
            } catch (_: Exception) {
                emptyList()
            }
        }

    /** Save device-selection intent. The desktop still needs to claim its
     * queue item and acquire the short execution lease before acting. */
    suspend fun requestPlacement(
        runId: String,
        deviceId: String,
        expectedRevision: Int,
    ): MobilePlacementOutcome = withContext(Dispatchers.IO) {
        val cleanRun = runId.trim().take(96)
        val cleanDevice = deviceId.trim().take(120)
        val base = base()
        val token = auth.currentToken()
        if (cleanRun.isBlank() || cleanDevice.isBlank() || expectedRevision < 1) {
            return@withContext MobilePlacementOutcome(false, "设备接力参数无效")
        }
        if (base.isBlank() || token.isNullOrBlank()) {
            return@withContext MobilePlacementOutcome(false, "连接服务器后才能选择在线电脑")
        }
        try {
            val payload = JSONObject()
                .put("device_id", cleanDevice)
                .put("expected_revision", expectedRevision)
                .toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val request = Request.Builder()
                .url("$base/api/work-runs/$cleanRun/placement")
                .header("Authorization", "Bearer $token")
                .post(payload)
                .build()
            val decoded = http.newCall(request).execute().use { response ->
                runCatching {
                    workJson.decodeFromString<MobilePlacementResponse>(
                        response.body?.string().orEmpty(),
                    )
                }.getOrNull()
            } ?: return@withContext MobilePlacementOutcome(
                false, "设备接力结果尚未确认，请刷新后重试",
            )
            if (decoded.ok) {
                auth.currentUserId()?.let { uid ->
                    local.setLastSync(uid, "$CANVAS_ETAG_PREFIX$cleanRun", "")
                }
                memoryAtMs = 0
                MobilePlacementOutcome(true)
            } else {
                MobilePlacementOutcome(
                    false,
                    when (decoded.error) {
                        "revision_conflict" -> "工作状态已经变化${decoded.currentRevision?.let { "（当前 r$it）" }.orEmpty()}，请刷新后再接力"
                        "already_leased" -> "这项工作已经由另一台电脑接管"
                        else -> "所选电脑已离线或当前账号无权使用"
                    },
                )
            }
        } catch (_: Exception) {
            MobilePlacementOutcome(false, "设备接力结果尚未确认，请刷新后重试")
        }
    }

    /** Publish only a server-verified paired-replay workflow. The button tap
     * is the explicit approval; the server still checks owner and revision. */
    suspend fun publishWorkflow(
        workflowId: String,
        expectedRevision: Int,
    ): MobileWorkflowOutcome = withContext(Dispatchers.IO) {
        val cleanId = workflowId.trim().take(96)
        val base = base()
        val token = auth.currentToken()
        if (cleanId.isBlank() || expectedRevision < 1) {
            return@withContext MobileWorkflowOutcome(false, "工作流状态无效")
        }
        if (base.isBlank() || token.isNullOrBlank()) {
            return@withContext MobileWorkflowOutcome(false, "连接服务器后才能发布工作流")
        }
        try {
            val payload = JSONObject()
                .put("expected_revision", expectedRevision)
                .put("approved", true)
                .put("parameters", JSONObject())
                .toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val request = Request.Builder()
                .url("$base/api/work-runs/workflows/$cleanId/publish")
                .header("Authorization", "Bearer $token")
                .post(payload)
                .build()
            val decoded = http.newCall(request).execute().use { response ->
                runCatching {
                    workJson.decodeFromString<MobileWorkflowMutationResponse>(
                        response.body?.string().orEmpty(),
                    )
                }.getOrNull()
            } ?: return@withContext MobileWorkflowOutcome(
                false, "工作流发布结果尚未确认，请刷新后重试",
            )
            if (decoded.ok) {
                memoryAtMs = 0
                MobileWorkflowOutcome(true)
            } else {
                MobileWorkflowOutcome(
                    false,
                    when (decoded.error) {
                        "revision_conflict" -> "工作流状态已经变化，请刷新后再确认"
                        "paired_replay_required" -> "还需要一次独立回放验证"
                        "explicit_approval_required" -> "需要你明确确认后才能发布"
                        else -> decoded.error ?: "工作流没有发布"
                    },
                )
            }
        } catch (_: Exception) {
            MobileWorkflowOutcome(false, "工作流发布结果尚未确认，请刷新后重试")
        }
    }

    /** Execute one revision-bound command. The UUID is generated and durably
     * queued before sending. Ambiguous responses are reconciled with the same
     * UUID, so the server can return its first admission without redispatch. */
    suspend fun command(runId: String, action: String, expectedRevision: Int): MobileWorkCommandOutcome {
        val cleanId = runId.trim().take(96)
        val safeAction = action.trim().lowercase()
        val uid = auth.currentUserId()
            ?: return MobileWorkCommandOutcome(false, "请先登录")
        if (cleanId.isBlank() || safeAction !in setOf("pause", "resume", "cancel", "retry") || expectedRevision < 1) {
            return MobileWorkCommandOutcome(false, "任务控制参数无效")
        }
        val commandId = "app-${UUID.randomUUID()}"
        val outboxItem = MobileWorkOutboxItem(
            id = commandId,
            kind = "command",
            runId = cleanId,
            action = safeAction,
            expectedRevision = expectedRevision,
        )
        outboxMutex.withLock { enqueueOutbox(uid, outboxItem) }
        return withContext(Dispatchers.IO) {
            val base = base()
            val token = auth.currentToken()
            if (base.isBlank() || token.isNullOrBlank()) {
                return@withContext MobileWorkCommandOutcome(false, "尚未连接服务器或登录已失效")
            }
            val payload = JSONObject()
                .put("command_id", commandId)
                .put("action", safeAction)
                .put("expected_revision", expectedRevision)
                .toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            try {
                val request = Request.Builder()
                    .url("$base/api/work-runs/$cleanId/commands")
                    .header("Authorization", "Bearer $token")
                    .post(payload)
                    .build()
                val response = http.newCall(request).execute()
                val decoded = response.use { r ->
                    val raw = r.body?.string().orEmpty()
                    runCatching { workJson.decodeFromString<MobileWorkCommandResponse>(raw) }.getOrNull()
                }
                if (decoded == null) {
                    outboxMutex.withLock { deferOutbox(uid, commandId) }
                    return@withContext MobileWorkCommandOutcome(
                        false,
                        "服务器返回了不兼容的控制结果；操作已保留，将使用同一请求标识重试",
                    )
                }
                outboxMutex.withLock { completeOutbox(uid, commandId) }
                decoded.run?.let { updated -> mutex.withLock { updateCachedRun(uid, updated) } }
                if (decoded.ok) {
                    local.setLastSync(uid, "$DETAIL_ETAG_PREFIX$cleanId", "")
                    local.setLastSync(uid, "$CANVAS_ETAG_PREFIX$cleanId", "")
                    memoryAtMs = 0
                    MobileWorkCommandOutcome(true, command = decoded.command, run = decoded.run)
                } else {
                    MobileWorkCommandOutcome(
                        false,
                        error = mobileControlError(decoded.error, decoded.currentRevision),
                        command = decoded.command,
                        run = decoded.run,
                    )
                }
            } catch (_: Exception) {
                outboxMutex.withLock { deferOutbox(uid, commandId) }
                // Reconciliation sends only this stable id. The backend
                // returns the first admission and never dispatches it twice.
                MobileWorkCommandOutcome(
                    false,
                    "操作结果尚未确认；已安全保存，联网后会用同一请求标识核对",
                )
            }
        }
    }

    /** Persist an explicit delivery decision. Ambiguous responses stay in the
     * owner-scoped outbox and are reconciled with the same decision id. */
    suspend fun decision(
        runId: String,
        action: String,
        expectedRevision: Int,
        note: String = "",
    ): MobileWorkDecisionOutcome {
        val cleanId = runId.trim().take(96)
        val safeAction = action.trim().lowercase()
        val safeNote = note.trim().take(800)
        val uid = auth.currentUserId()
            ?: return MobileWorkDecisionOutcome(false, "请先登录")
        if (
            cleanId.isBlank() ||
            safeAction !in setOf("accept_delivery", "request_changes") ||
            expectedRevision < 1
        ) {
            return MobileWorkDecisionOutcome(false, "验收参数无效")
        }
        if (safeAction == "request_changes" && safeNote.length < 2) {
            return MobileWorkDecisionOutcome(false, "请写明需要修改的内容")
        }
        val decisionId = "app-review-${UUID.randomUUID()}"
        val outboxItem = MobileWorkOutboxItem(
            id = decisionId,
            kind = "decision",
            runId = cleanId,
            action = safeAction,
            expectedRevision = expectedRevision,
            note = safeNote,
        )
        outboxMutex.withLock { enqueueOutbox(uid, outboxItem) }
        return withContext(Dispatchers.IO) {
            val base = base()
            val token = auth.currentToken()
            if (base.isBlank() || token.isNullOrBlank()) {
                return@withContext MobileWorkDecisionOutcome(false, "尚未连接服务器或登录已失效")
            }
            val payload = JSONObject()
                .put("decision_id", decisionId)
                .put("action", safeAction)
                .put("expected_revision", expectedRevision)
                .put("note", safeNote)
                .toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            try {
                val request = Request.Builder()
                    .url("$base/api/work-runs/$cleanId/decisions")
                    .header("Authorization", "Bearer $token")
                    .post(payload)
                    .build()
                val response = http.newCall(request).execute()
                val decoded = response.use { r ->
                    val raw = r.body?.string().orEmpty()
                    runCatching {
                        workJson.decodeFromString<MobileWorkDecisionResponse>(raw)
                    }.getOrNull()
                }
                if (decoded == null) {
                    outboxMutex.withLock { deferOutbox(uid, decisionId) }
                    return@withContext MobileWorkDecisionOutcome(
                        false,
                        "服务器返回了不兼容的验收结果；决定已保留，将使用同一请求标识重试",
                    )
                }
                outboxMutex.withLock { completeOutbox(uid, decisionId) }
                decoded.run?.let { updated -> mutex.withLock { updateCachedRun(uid, updated) } }
                if (decoded.ok) {
                    local.setLastSync(uid, "$DETAIL_ETAG_PREFIX$cleanId", "")
                    local.setLastSync(uid, "$CANVAS_ETAG_PREFIX$cleanId", "")
                    memoryAtMs = 0
                    MobileWorkDecisionOutcome(
                        true,
                        decision = decoded.decision,
                        run = decoded.run,
                    )
                } else {
                    MobileWorkDecisionOutcome(
                        false,
                        error = mobileDecisionError(
                            decoded.error, decoded.reason, decoded.currentRevision,
                        ),
                        decision = decoded.decision,
                        run = decoded.run,
                    )
                }
            } catch (_: Exception) {
                outboxMutex.withLock { deferOutbox(uid, decisionId) }
                MobileWorkDecisionOutcome(
                    false,
                    "验收结果尚未确认；已安全保存，联网后会用同一请求标识核对",
                )
            }
        }
    }

    /** Save a revision-bound artifact annotation. Ambiguous network outcomes
     * remain in the encrypted owner outbox and reuse the same idempotency key. */
    suspend fun annotate(
        runId: String,
        artifactId: String,
        artifactRevision: Int,
        note: String,
        expectedRevision: Int,
    ): MobileWorkAnnotationOutcome {
        val cleanRun = runId.trim().take(96)
        val cleanArtifact = artifactId.trim().take(160)
        val cleanNote = note.trim().take(800)
        val uid = auth.currentUserId()
            ?: return MobileWorkAnnotationOutcome(false, "请先登录")
        if (
            cleanRun.isBlank() || cleanArtifact.length < 3 ||
            artifactRevision < 1 || expectedRevision < 1 || cleanNote.length < 2
        ) {
            return MobileWorkAnnotationOutcome(false, "成果批注参数无效")
        }
        val annotationId = "app-note-${UUID.randomUUID()}"
        val item = MobileWorkOutboxItem(
            id = annotationId,
            kind = "annotation",
            runId = cleanRun,
            action = "annotate",
            expectedRevision = expectedRevision,
            note = cleanNote,
            artifactId = cleanArtifact,
            artifactRevision = artifactRevision,
        )
        outboxMutex.withLock { enqueueOutbox(uid, item) }
        return withContext(Dispatchers.IO) {
            val base = base()
            val token = auth.currentToken()
            if (base.isBlank() || token.isNullOrBlank()) {
                return@withContext MobileWorkAnnotationOutcome(
                    false, "批注已安全保存，连接恢复后会继续提交",
                )
            }
            val payload = annotationPayload(item)
            try {
                val request = Request.Builder()
                    .url("$base/api/work-runs/$cleanRun/annotations")
                    .header("Authorization", "Bearer $token")
                    .post(payload)
                    .build()
                val decoded = http.newCall(request).execute().use { response ->
                    runCatching {
                        workJson.decodeFromString<MobileWorkAnnotationResponse>(
                            response.body?.string().orEmpty(),
                        )
                    }.getOrNull()
                }
                if (decoded == null) {
                    outboxMutex.withLock { deferOutbox(uid, annotationId) }
                    return@withContext MobileWorkAnnotationOutcome(
                        false, "批注结果尚未确认；已安全保存并会使用同一标识核对",
                    )
                }
                outboxMutex.withLock { completeOutbox(uid, annotationId) }
                local.setLastSync(uid, "$CANVAS_ETAG_PREFIX$cleanRun", "")
                memoryAtMs = 0
                if (decoded.ok) {
                    MobileWorkAnnotationOutcome(true, annotation = decoded.annotation)
                } else {
                    MobileWorkAnnotationOutcome(
                        false,
                        when (decoded.error) {
                            "revision_conflict" -> "工作状态已经变化${decoded.currentRevision?.let { "（当前 r$it）" }.orEmpty()}，请刷新后再批注"
                            "artifact_not_found", "not_found" -> "成果不存在或当前账号无权修改"
                            else -> "成果批注没有保存"
                        },
                    )
                }
            } catch (_: Exception) {
                outboxMutex.withLock { deferOutbox(uid, annotationId) }
                MobileWorkAnnotationOutcome(
                    false, "批注结果尚未确认；已安全保存并会使用同一标识核对",
                )
            }
        }
    }

    private fun annotationPayload(item: MobileWorkOutboxItem) = JSONObject()
        .put("annotation_id", item.id)
        .put("idempotency_key", "annotation:${item.id}")
        .put("artifact_id", item.artifactId)
        .put("artifact_revision", item.artifactRevision)
        .put("target", JSONObject().put("kind", "artifact"))
        .put("note", item.note)
        .put("expected_revision", item.expectedRevision)
        .toString()
        .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())

    private fun readOutbox(uid: String): MobileWorkOutbox =
        local.getWorkRuntimeOutbox(uid)?.let {
            runCatching { workJson.decodeFromString<MobileWorkOutbox>(it) }.getOrNull()
        }?.takeIf { it.contract == "hashmm.mobile-work-outbox.v1" }
            ?: MobileWorkOutbox()

    private fun writeOutbox(uid: String, value: MobileWorkOutbox) {
        local.putWorkRuntimeOutbox(uid, workJson.encodeToString(value))
    }

    private fun enqueueOutbox(uid: String, item: MobileWorkOutboxItem) {
        writeOutbox(uid, mobileWorkOutboxUpsert(readOutbox(uid), item))
    }

    private fun completeOutbox(uid: String, id: String) {
        writeOutbox(uid, mobileWorkOutboxRemove(readOutbox(uid), id))
    }

    private fun deferOutbox(uid: String, id: String) {
        val outbox = readOutbox(uid)
        val now = System.currentTimeMillis()
        val next = outbox.items.map { item ->
            if (item.id != id) item else {
                val attempts = item.attempts + 1
                item.copy(
                    attempts = attempts,
                    nextAttemptAt = now + mobileWorkOutboxBackoffMs(attempts),
                )
            }
        }
        writeOutbox(uid, outbox.copy(items = next))
    }

    /** Resolve ambiguous responses by replaying only the same durable id.
     * This is reconciliation, not side-effect replay: server admission is
     * idempotent and returns the first command/decision record. */
    private suspend fun reconcileOutbox(uid: String): Int = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) {
            return@withContext outboxMutex.withLock { readOutbox(uid).items.size }
        }
        val now = System.currentTimeMillis()
        val candidates = outboxMutex.withLock {
            readOutbox(uid).items.filter { it.nextAttemptAt <= now }.take(8)
        }
        for (item in candidates) {
            val endpoint = when (item.kind) {
                "command" -> "commands"
                "decision" -> "decisions"
                "annotation" -> "annotations"
                else -> {
                    outboxMutex.withLock { completeOutbox(uid, item.id) }
                    continue
                }
            }
            val payload = if (item.kind == "annotation") {
                annotationPayload(item)
            } else {
                JSONObject()
                    .put(
                        if (item.kind == "command") "command_id" else "decision_id",
                        item.id,
                    )
                    .put("action", item.action)
                    .put("expected_revision", item.expectedRevision)
                    .also { if (item.kind == "decision") it.put("note", item.note) }
                    .toString()
                    .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            }
            try {
                val request = Request.Builder()
                    .url("$base/api/work-runs/${item.runId}/$endpoint")
                    .header("Authorization", "Bearer $token")
                    .post(payload)
                    .build()
                val definitive = http.newCall(request).execute().use { response ->
                    val raw = response.body?.string().orEmpty()
                    if (item.kind == "command") {
                        runCatching {
                            workJson.decodeFromString<MobileWorkCommandResponse>(raw)
                        }.getOrNull() != null
                    } else if (item.kind == "decision") {
                        runCatching {
                            workJson.decodeFromString<MobileWorkDecisionResponse>(raw)
                        }.getOrNull() != null
                    } else {
                        runCatching {
                            workJson.decodeFromString<MobileWorkAnnotationResponse>(raw)
                        }.getOrNull() != null
                    }
                }
                outboxMutex.withLock {
                    if (definitive) completeOutbox(uid, item.id)
                    else deferOutbox(uid, item.id)
                }
            } catch (_: Exception) {
                outboxMutex.withLock { deferOutbox(uid, item.id) }
            }
        }
        outboxMutex.withLock { readOutbox(uid).items.size }
    }

    private fun updateCachedRun(uid: String, run: MobileWorkRun) {
        ensureMemoryOwner(uid)
        val state = memory ?: local.getWorkRuntimeSnapshot(uid)?.let {
            runCatching { workJson.decodeFromString<MobileWorkSnapshot>(it) }.getOrNull()
        } ?: MobileWorkSnapshot()
        val cleanRun = run.copy(events = emptyList())
        val runs = (state.runs.filterNot { it.id == run.id } + cleanRun)
            .sortedByDescending { it.updatedAt }.take(MAX_RUNS)
        val events = if (run.events.isNotEmpty()) {
            state.eventsByRun + (run.id to run.events.takeLast(MAX_EVENTS))
        } else state.eventsByRun
        val saved = state.copy(
            runs = runs,
            eventsByRun = events,
            canvasesByRun = state.canvasesByRun - run.id,
        )
        local.putWorkRuntimeSnapshot(uid, workJson.encodeToString(saved))
        memory = saved
    }

    private fun ensureMemoryOwner(uid: String) {
        if (!workMemoryOwnerChanged(memoryOwnerId, uid)) return
        memory = null
        memoryAtMs = 0
        memoryOwnerId = uid
    }

    private suspend fun base(): String {
        val raw = settings.clientUrl.first().trim().trimEnd('/')
        if (raw.isBlank()) return ""
        val schemeless = raw.removePrefix("https://").removePrefix("http://")
        val host = schemeless.substringBefore('/').substringBefore(':')
        val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(host)
        return when {
            isIp -> "http://$schemeless"
            raw.startsWith("http://") || raw.startsWith("https://") -> raw
            else -> "https://$raw"
        }
    }

    /**
     * Prefer the owner-scoped V2 workspace projection. It is backed by the
     * same WorkRuntime ledger as Chat and desktop; the App only converts the
     * user-facing state names into its existing encrypted local projection.
     * A 404 is the sole compatibility fallback to the V1 feed.
     */
    private fun fetchWorkspaceV2(
        base: String,
        token: String,
        uid: String,
        cached: MobileWorkSnapshot,
    ): Pair<Boolean, MobileWorkSnapshot?> {
        val builder = Request.Builder()
            .url("$base/api/v2/workspaces/personal/snapshot")
            .header("Authorization", "Bearer $token")
        if (cached.runs.isNotEmpty()) {
            local.getLastSync(uid, WORKSPACE_ETAG_KEY)
                .takeIf { it.startsWith('"') }
                ?.let { builder.header("If-None-Match", it) }
        }
        return try {
            http.newCall(builder.get().build()).execute().use { response ->
                when {
                    response.code == 304 -> true to cached
                    response.code == 404 -> false to null
                    !response.isSuccessful -> true to MobileWorkSnapshot(
                        error = "工作空间读取失败（${response.code}）",
                    )
                    else -> {
                        response.header("ETag")?.takeIf { it.isNotBlank() }
                            ?.let { local.setLastSync(uid, WORKSPACE_ETAG_KEY, it) }
                        val payload = runCatching {
                            workJson.decodeFromString<MobileWorkspaceSnapshotV2>(
                                response.body?.string().orEmpty(),
                            )
                        }.getOrNull()
                        if (payload == null || !payload.isTrustedWorkspace()) {
                            true to MobileWorkSnapshot(error = "服务器返回了不兼容的工作空间")
                        } else {
                            val feed = payload.toLegacyFeed()
                            val runs = feed.items
                                .distinctBy { it.id }
                                .sortedByDescending { it.updatedAt }
                                .take(MAX_RUNS)
                            true to MobileWorkSnapshot(
                                cursor = payload.sync.nextCursor,
                                highWaterCursor = payload.sync.highWaterCursor,
                                runs = runs,
                                eventsByRun = cached.eventsByRun.filterKeys { id -> runs.any { it.id == id } },
                                canvasesByRun = cached.canvasesByRun.filterKeys { id -> runs.any { it.id == id } },
                                actionInbox = feed.actionInbox,
                            )
                        }
                    }
                }
            }
        } catch (_: Exception) {
            true to MobileWorkSnapshot(error = "网络连接失败")
        }
    }

    private suspend fun fetchRemote(uid: String, cached: MobileWorkSnapshot): MobileWorkSnapshot = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) {
            return@withContext MobileWorkSnapshot(error = "尚未连接服务器或登录已失效")
        }
        val (workspaceSupported, workspace) = fetchWorkspaceV2(base, token, uid, cached)
        if (workspaceSupported) {
            return@withContext workspace ?: MobileWorkSnapshot(error = "工作空间暂时不可用")
        }
        val merged = cached.runs.associateBy { it.id }.toMutableMap()
        var cursor = cached.cursor
        var highWater = cached.highWaterCursor
        var authoritativeInbox = cached.actionInbox
        var pages = 0
        try {
            do {
                val builder = Request.Builder()
                    .url("$base/api/work-runs?after_cursor=$cursor&limit=100")
                    .header("Authorization", "Bearer $token")
                if (pages == 0 && cached.runs.isNotEmpty()) {
                    local.getLastSync(uid, ETAG_KEY).takeIf { it.startsWith('"') }
                        ?.let { builder.header("If-None-Match", it) }
                }
                val response = http.newCall(builder.get().build()).execute()
                val page = response.use { r ->
                    if (r.code == 304) return@withContext cached
                    if (r.code == 404) return@withContext MobileWorkSnapshot(error = "服务器版本尚未提供统一工作状态")
                    if (!r.isSuccessful) return@withContext MobileWorkSnapshot(error = "工作状态读取失败（${r.code}）")
                    if (pages == 0) r.header("ETag")?.takeIf { it.isNotBlank() }
                        ?.let { local.setLastSync(uid, ETAG_KEY, it) }
                    parseMobileWorkFeed(r.body?.string().orEmpty())
                        ?: return@withContext MobileWorkSnapshot(error = "服务器返回了不兼容的工作状态")
                }
                page.items.forEach { merged[it.id] = it }
                if (pages == 0 && page.actionInbox.schema == "hashmm.action-inbox.v1") {
                    authoritativeInbox = page.actionInbox
                }
                cursor = page.nextCursor
                highWater = page.highWaterCursor
                pages += 1
                val more = page.hasMore && pages < MAX_PAGES
            } while (more)
            val selectedRuns = merged.values.sortedByDescending { it.updatedAt }.take(MAX_RUNS)
            val selectedIds = selectedRuns.mapTo(mutableSetOf()) { it.id }
            MobileWorkSnapshot(
                cursor = cursor,
                highWaterCursor = highWater,
                runs = selectedRuns,
                eventsByRun = cached.eventsByRun.filterKeys { it in selectedIds },
                canvasesByRun = cached.canvasesByRun.filterKeys { it in selectedIds },
                actionInbox = authoritativeInbox,
            )
        } catch (_: Exception) {
            MobileWorkSnapshot(error = "网络连接失败")
        }
    }

    private suspend fun fetchRemoteDetail(
        uid: String,
        runId: String,
        cachedRun: MobileWorkRun?,
        cachedEvents: List<MobileWorkEvent>,
        force: Boolean,
    ): MobileWorkRun? = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        var cursor = if (force) 0 else (cachedEvents.maxOfOrNull { it.seq } ?: 0)
        val merged = cachedEvents.associateBy { it.seq }.toMutableMap()
        var latest = cachedRun
        var pages = 0
        try {
            var more: Boolean
            do {
                val builder = Request.Builder()
                    .url("$base/api/work-runs/$runId?after_seq=$cursor&limit=100")
                    .header("Authorization", "Bearer $token")
                if (!force && pages == 0 && cachedRun != null) {
                    local.getLastSync(uid, "$DETAIL_ETAG_PREFIX$runId")
                        .takeIf { it.startsWith("\"") }
                        ?.let { builder.header("If-None-Match", it) }
                }
                val response = http.newCall(builder.get().build()).execute()
                val page = response.use { r ->
                    if (r.code == 304) return@withContext cachedRun?.copy(events = cachedEvents)
                    if (r.code == 404 || r.code == 403) return@withContext null
                    if (!r.isSuccessful) return@withContext null
                    if (pages == 0) r.header("ETag")?.takeIf { it.isNotBlank() }
                        ?.let { local.setLastSync(uid, "$DETAIL_ETAG_PREFIX$runId", it) }
                    parseMobileWorkRun(r.body?.string().orEmpty()) ?: return@withContext null
                }
                page.events.forEach { event -> if (event.seq > 0) merged[event.seq] = event }
                latest = page.copy(events = emptyList())
                cursor = page.events.maxOfOrNull { it.seq } ?: cursor
                pages += 1
                more = page.eventsTruncated && pages < MAX_DETAIL_PAGES
            } while (more)
            latest.copy(events = merged.values.sortedBy { it.seq }.takeLast(MAX_EVENTS))
        } catch (_: Exception) {
            cachedRun?.copy(events = cachedEvents)
        }
    }

    private suspend fun fetchRemoteWorkspace(
        uid: String,
        runId: String,
        cached: MobileWorkCanvas?,
        force: Boolean,
    ): MobileWorkCanvasLookup = withContext(Dispatchers.IO) {
        val base = base()
        var token = auth.currentToken()
        if (base.isBlank()) return@withContext MobileWorkCanvasLookup(
            error = "尚未配置 HashMM 服务器地址",
        )
        if (token.isNullOrBlank()) return@withContext MobileWorkCanvasLookup(
            error = "登录状态不可用，请重新登录",
        )
        try {
            fun request(accessToken: String): Request {
                val builder = Request.Builder()
                    .url("$base/api/work-runs/$runId/workspace")
                    .header("Authorization", "Bearer $accessToken")
                if (!force && cached != null) {
                    local.getLastSync(uid, "$CANVAS_ETAG_PREFIX$runId")
                        .takeIf { it.startsWith("\"") }
                        ?.let { builder.header("If-None-Match", it) }
                }
                return builder.get().build()
            }
            var response = http.newCall(request(token)).execute()
            if (response.code == 401) {
                response.close()
                val refreshed = auth.refreshAccessToken()
                if (!refreshed.isNullOrBlank()) {
                    token = refreshed
                    response = http.newCall(request(refreshed)).execute()
                } else {
                    return@withContext MobileWorkCanvasLookup(
                        error = "登录状态已过期，请重新登录",
                    )
                }
            }
            response.use { r ->
                if (r.code == 304) return@withContext MobileWorkCanvasLookup(canvas = cached)
                if (r.code == 401) return@withContext MobileWorkCanvasLookup(error = "登录状态已过期，请重新登录")
                if (r.code == 403) return@withContext MobileWorkCanvasLookup(error = "当前账号无权查看这项工作")
                if (r.code == 404) return@withContext MobileWorkCanvasLookup(error = "这项工作不存在或已归档")
                if (r.code == 429) return@withContext MobileWorkCanvasLookup(error = "服务器繁忙，请稍后重试")
                if (!r.isSuccessful) {
                    return@withContext MobileWorkCanvasLookup(
                        error = if (r.code >= 500) "服务器暂时不可用，请稍后重试" else "工作读取失败（HTTP ${r.code}）",
                    )
                }
                val parsed = parseMobileWorkCanvas(r.body?.string().orEmpty())
                    ?: return@withContext MobileWorkCanvasLookup(error = "服务器返回的数据格式不兼容")
                if (parsed.runId != runId) return@withContext MobileWorkCanvasLookup(error = "服务器返回了错误的工作编号")
                r.header("ETag")?.takeIf { it.isNotBlank() }?.let {
                    local.setLastSync(uid, "$CANVAS_ETAG_PREFIX$runId", it)
                }
                MobileWorkCanvasLookup(canvas = parsed)
            }
        } catch (_: Exception) {
            MobileWorkCanvasLookup(
                canvas = cached,
                error = if (cached == null) "无法连接 HashMM 服务器，请检查网络后重试" else null,
            )
        }
    }

    private companion object {
        const val MEMORY_TTL_MS = 20_000L
        const val MAX_PAGES = 5
        const val MAX_DETAIL_PAGES = 3
        const val MAX_RUNS = 80
        const val MAX_EVENTS = 160
        const val ETAG_KEY = "work_runtime_etag"
        const val WORKSPACE_ETAG_KEY = "workspace_v2_etag"
        const val DETAIL_ETAG_PREFIX = "work_run_etag_"
        const val CANVAS_ETAG_PREFIX = "work_canvas_etag_"
    }
}
