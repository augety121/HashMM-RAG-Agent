package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.FactCheck
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import com.hashmm.app.data.remote.MobileWorkEvent
import com.hashmm.app.data.remote.MobileWorkRun
import com.hashmm.app.data.remote.MobileWorkSnapshot
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * 当前账号的统一工作账本。Chat、长任务和多 Agent 使用同一状态协议，
 * 事件按游标增量读取并缓存到账号隔离的加密本地存储。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RunsScreen(
    onBack: () -> Unit,
    onOpenConversation: (String) -> Unit,
    viewModel: NativeFeedViewModel = hiltViewModel(),
) {
    val scope = rememberCoroutineScope()
    var snapshot by remember { mutableStateOf<MobileWorkSnapshot?>(null) }
    var refreshing by remember { mutableStateOf(false) }
    var expandedRun by rememberSaveable { mutableStateOf("") }
    val details = remember { mutableStateMapOf<String, MobileWorkRun>() }
    val loadingDetails = remember { mutableStateMapOf<String, Boolean>() }
    val commandErrors = remember { mutableStateMapOf<String, String>() }
    var commandBusy by remember { mutableStateOf("") }

    fun reload(force: Boolean = false) {
        scope.launch {
            refreshing = true
            snapshot = viewModel.workRuns(force)
            refreshing = false
        }
    }
    fun toggle(run: MobileWorkRun) {
        if (expandedRun == run.id) {
            expandedRun = ""
            return
        }
        expandedRun = run.id
        if (details[run.id] == null) {
            loadingDetails[run.id] = true
            scope.launch {
                viewModel.workRunDetail(run.id)?.let { details[run.id] = it }
                loadingDetails.remove(run.id)
            }
        }
    }
    fun command(run: MobileWorkRun, action: String) {
        val current = details[run.id] ?: run
        if (commandBusy.isNotBlank() || action !in current.control.availableActions) return
        commandBusy = run.id
        commandErrors.remove(run.id)
        scope.launch {
            val outcome = viewModel.workRunCommand(run.id, action, current.control.expectedRevision)
            outcome.run?.let { details[run.id] = it }
            if (outcome.ok) {
                snapshot = viewModel.workRuns(force = true)
                viewModel.workRunDetail(run.id, force = true)?.let { details[run.id] = it }
            } else {
                commandErrors[run.id] = outcome.error ?: "任务控制失败"
                if (outcome.run == null) {
                    viewModel.workRunDetail(run.id, force = true)?.let { details[run.id] = it }
                }
            }
            commandBusy = ""
        }
    }

    LaunchedEffect(Unit) { reload() }
    val state = snapshot
    val runs = state?.runs.orEmpty().sortedByDescending { it.updatedAt }

    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        ModuleHeader(Icons.Outlined.Bolt, "运行轨迹", "Chat、长任务和多 Agent 的同一份工作记录", onBack)
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { reload(force = true) },
            modifier = Modifier.fillMaxSize(),
        ) {
            when {
                state == null -> HmmStateView(kind = HmmStateKind.Loading, title = "读取工作记录")
                state.error != null -> HmmStateView(
                    kind = HmmStateKind.Error,
                    title = "工作记录暂不可用",
                    message = state.error,
                    onRetry = { reload(force = true) },
                )
                runs.isEmpty() -> HmmStateView(
                    kind = HmmStateKind.Empty,
                    icon = Icons.Outlined.Bolt,
                    title = "还没有工作记录",
                    message = "从 Chat 发起问答、长任务或多 Agent 协作后，这里会显示真实进度；不会用演示数据填充。",
                )
                else -> Column(Modifier.fillMaxSize()) {
                    val active = runs.count { it.status in activeRunStates }
                    val delivered = runs.count { it.status in setOf("delivered", "completed") }
                    StatTriple(
                        "最近记录" to "${runs.size}",
                        "处理中" to "$active",
                        "已交付" to "$delivered",
                        toneB = if (active > 0) KitTone.Warn else KitTone.Success,
                    )
                    state.notice?.takeIf { it.isNotBlank() }?.let {
                        Text(
                            it,
                            Modifier.padding(horizontal = 18.dp, vertical = 7.dp),
                            fontSize = 11.5.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Spacer(Modifier.height(10.dp))
                    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                        item { ModuleSectionLabel("统一工作账本", "按服务器事件游标增量同步") }
                        item {
                            KitGroup {
                                runs.forEachIndexed { index, run ->
                                    if (index > 0) KitInsetDivider()
                                    KitRow(
                                        icon = workRunIcon(run.kind),
                                        title = run.title.ifBlank { workRunKind(run.kind) },
                                        sub = "${workRunKind(run.kind)} · r${run.revision} · e${run.eventCursor}",
                                        tone = workRunTone(run.status),
                                        badge = mobileWorkStatus(run.status),
                                        badgeTone = workRunTone(run.status),
                                        right = kitAgo(run.updatedAt.toLong()),
                                        onClick = { toggle(run) },
                                    )
                                    if (expandedRun == run.id) {
                                        WorkRunDetailPanel(
                                            run = details[run.id] ?: run,
                                            loading = loadingDetails[run.id] == true,
                                            onRefresh = {
                                                loadingDetails[run.id] = true
                                                scope.launch {
                                                    viewModel.workRunDetail(run.id, force = true)?.let { details[run.id] = it }
                                                    loadingDetails.remove(run.id)
                                                }
                                            },
                                            onOpenConversation = onOpenConversation,
                                            commandBusy = commandBusy == run.id,
                                            commandError = commandErrors[run.id].orEmpty(),
                                            onCommand = { action -> command(run, action) },
                                        )
                                    }
                                }
                            }
                        }
                        item { Spacer(Modifier.height(16.dp)) }
                    }
                }
            }
        }
    }
}

@Composable
private fun WorkRunDetailPanel(
    run: MobileWorkRun,
    loading: Boolean,
    onRefresh: () -> Unit,
    onOpenConversation: (String) -> Unit,
    commandBusy: Boolean,
    commandError: String,
    onCommand: (String) -> Unit,
) {
    val gate = run.snapshot.objectValue("completion_gate")
        ?: run.snapshot.objectValue("run_manifest")?.objectValue("completion_gate")
    val retrieval = mobileRetrievalSummary(run.snapshot)
    val harness = mobileHarnessSummary(run.snapshot)
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth().padding(start = 14.dp, end = 14.dp, bottom = 12.dp),
    ) {
        Column(Modifier.padding(13.dp)) {
            Row(Modifier.fillMaxWidth()) {
                Text("本次工作的确定性记录", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f))
                if (loading) CircularProgressIndicator(Modifier.size(15.dp), strokeWidth = 2.dp)
            }
            Spacer(Modifier.height(8.dp))
            RunFact("类型", workRunKind(run.kind))
            RunFact("状态", mobileWorkStatus(run.status))
            RunFact("状态版本", "r${run.revision} · 事件 e${run.eventCursor}")
            if (run.control.schema == "hashmm.work-control.v1") {
                RunFact("控制边界", mobileWorkBoundary(run.control.sideEffectBoundary))
                if (run.control.latestCommand?.status == "uncertain") {
                    Text(
                        "上次控制在断线前没有确认。系统不会自动重放，请先刷新并核对当前状态。",
                        fontSize = 10.5.sp,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
            gate?.let {
                val verified = it.boolValue("can_claim_verified")
                RunFact("完成门", if (verified == true) "已通过，可声明完成" else "尚未通过或等待验收")
                it.textValue("status").takeIf(String::isNotBlank)?.let { value -> RunFact("核验状态", value) }
            }
            harness?.let {
                Spacer(Modifier.height(7.dp))
                Text("Agent 运行内核", fontSize = 11.5.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold)
                RunFact("终止结果", it.terminalReason.ifBlank { "运行中" })
                RunFact("真实能力", "${it.effectiveTools}/${it.declaredTools} 项可执行")
                RunFact("生命周期", "${it.events} 个事件 · ${it.toolCalls} 次工具 · ${it.compactions} 次压缩")
                if (it.contextGeneration > 0 || it.contextCheckpointed) {
                    RunFact(
                        "上下文恢复",
                        "第 ${it.contextGeneration} 代 · ${if (it.contextCheckpointed) "检查点已保存" else "尚未保存检查点"}",
                    )
                }
                RunFact("子 Agent", "${it.children}/${it.childLimit} 个")
                RunFact("审批与联网", "${it.approvalMode.ifBlank { "未记录" }} · ${it.networkMode.ifBlank { "未记录" }}")
                if (it.missingExecutors > 0) {
                    Text(
                        "${it.missingExecutors} 项能力只有定义、没有执行器，已在模型调用前移除。",
                        fontSize = 10.5.sp,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 5.dp),
                    )
                }
                Text(
                    "轨迹只保存状态、耗时和参数指纹，不保存原始工具参数、凭据或工具正文。",
                    fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            retrieval?.let {
                Spacer(Modifier.height(7.dp))
                Text("检索证据", fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold)
                RunFact("检索状态", it.statusLabel)
                RunFact("实际路线", it.route.ifBlank { "未记录" })
                RunFact("候选与证据", "${it.totalCandidates} 个候选 · ${it.evidenceCount} 条证据")
                RunFact("查询尝试", "${it.attempts} 次 · ${formatRunDuration(it.elapsedMs.toLong())}")
                RunFact("文档范围", if (it.aclScoped) "已按账号权限过滤" else "未记录权限过滤证据")
                if (it.graphAdded > 0 || it.graphSkipped > 0) {
                    RunFact("图谱扩展", "新增 ${it.graphAdded} 条原文证据 · 跳过 ${it.graphSkipped} 条不可用节点")
                }
                if (it.degradations > 0) {
                    Text(
                        "本轮有 ${it.degradations} 项检索降级；这里只展示服务端实际记录，不根据回答质量猜测原因。",
                        fontSize = 10.5.sp,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 5.dp),
                    )
                }
            }
            if (run.events.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text("最近事件", fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold)
                run.events.takeLast(10).forEach { WorkEventRow(it) }
                if (run.eventsTruncated) {
                    Text("较早事件已折叠，可刷新继续同步。", fontSize = 10.5.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else if (!loading) {
                Text(
                    "尚未取得事件明细。点击刷新会从服务器增量读取，不会重新执行任务。",
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (run.control.availableActions.isNotEmpty()) {
                Spacer(Modifier.height(9.dp))
                Text("任务控制", fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold)
                run.control.availableActions.forEach { action ->
                    Spacer(Modifier.height(6.dp))
                    OutlinedButton(
                        onClick = { onCommand(action) },
                        enabled = !commandBusy,
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        if (commandBusy) {
                            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                            Spacer(Modifier.size(7.dp))
                        }
                        Text(mobileWorkAction(action))
                    }
                }
                if (commandError.isNotBlank()) {
                    Text(
                        commandError,
                        fontSize = 10.5.sp,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
            Spacer(Modifier.height(9.dp))
            Button(onClick = onRefresh, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) {
                Text("刷新事件")
            }
            if (run.conversationId.isNotBlank()) {
                Spacer(Modifier.height(7.dp))
                Button(
                    onClick = { onOpenConversation(run.conversationId) },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                ) { Text("打开原对话") }
            }
        }
    }
}

@Composable
private fun WorkEventRow(event: MobileWorkEvent) {
    val sessionDetail = mobileAgentSessionDetail(event)
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text("e${event.seq}", fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.fillMaxWidth(0.16f))
        Column(Modifier.weight(1f)) {
            Text(event.summary.ifBlank { event.type }, fontSize = 11.5.sp,
                color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.Medium)
            Text(
                listOf(event.type, event.status).filter { it.isNotBlank() }.joinToString(" · "),
                fontSize = 10.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (sessionDetail.isNotBlank()) {
                Text(
                    sessionDetail,
                    fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

internal fun mobileAgentSessionDetail(event: MobileWorkEvent): String {
    if (event.type != "agent_session") return ""
    val role = event.payload.textValue("role")
    val status = when (event.payload.textValue("session_status")) {
        "queued" -> "等待"
        "running" -> "执行中"
        "completed" -> "完成"
        "failed" -> "失败"
        "stopped" -> "已停止"
        else -> "状态未记录"
    }
    val calls = event.payload.intValue("tool_calls")
    val tools = (event.payload["allowed_tools"] as? JsonArray)?.size ?: 0
    return listOfNotNull(
        role.takeIf(String::isNotBlank), status,
        "$calls 次工具调用", "$tools 项授权能力",
    ).joinToString(" · ")
}

@Composable
private fun RunFact(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(label, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.fillMaxWidth(0.28f))
        Text(value, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurface,
            fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
    }
}

private val activeRunStates = setOf("queued", "running", "waiting_input", "waiting_approval", "blocked")

private fun workRunKind(kind: String): String = when (kind) {
    "chat" -> "Chat"
    "loop" -> "长任务"
    "team" -> "多 Agent"
    "browser" -> "浏览器工作"
    "computer" -> "电脑操作"
    "artifact" -> "产物"
    else -> "工作流"
}

private fun workRunIcon(kind: String): ImageVector = when (kind) {
    "chat" -> Icons.Outlined.ChatBubbleOutline
    "loop" -> Icons.Outlined.Schedule
    "team" -> Icons.Outlined.Groups
    "browser" -> Icons.Outlined.Language
    "computer" -> Icons.Outlined.Computer
    "artifact" -> Icons.Outlined.Description
    else -> Icons.Outlined.Hub
}

private fun workRunTone(status: String): KitTone = when (status) {
    "failed", "blocked" -> KitTone.Error
    "queued", "running", "waiting_input", "waiting_approval", "interrupted" -> KitTone.Warn
    "observed" -> KitTone.Neutral
    "delivered", "completed" -> KitTone.Success
    "checks_passed" -> KitTone.Success
    "delivered_with_limits", "needs_attention" -> KitTone.Warn
    else -> KitTone.Neutral
}

internal fun mobileWorkAction(action: String): String = when (action) {
    "pause" -> "暂停并保存检查点"
    "resume" -> "从检查点继续"
    "cancel" -> "停止任务"
    "retry" -> "重新派发为新任务"
    else -> "不支持的操作"
}

internal fun mobileWorkBoundary(boundary: String): String = when (boundary) {
    "checkpointed_cooperative" -> "保存检查点后协作式暂停"
    "cooperative_after_current_model_call" -> "当前模型调用返回后停止"
    "cancel_before_desktop_claim_only" -> "仅桌面端领取前可取消"
    else -> "只读查看"
}

private fun JsonObject.objectValue(key: String): JsonObject? = this[key] as? JsonObject
private fun JsonObject.textValue(key: String): String = this[key]?.jsonPrimitive?.contentOrNull.orEmpty()
private fun JsonObject.boolValue(key: String): Boolean? = this[key]?.jsonPrimitive?.booleanOrNull
private fun JsonObject.intValue(key: String): Int = this[key]?.jsonPrimitive?.intOrNull ?: 0

internal data class MobileRetrievalSummary(
    val statusLabel: String,
    val route: String,
    val evidenceCount: Int,
    val totalCandidates: Int,
    val attempts: Int,
    val degradations: Int,
    val elapsedMs: Int,
    val aclScoped: Boolean,
    val graphAdded: Int,
    val graphSkipped: Int,
)

internal data class MobileHarnessSummary(
    val terminalReason: String,
    val effectiveTools: Int,
    val declaredTools: Int,
    val missingExecutors: Int,
    val events: Int,
    val toolCalls: Int,
    val compactions: Int,
    val children: Int,
    val childLimit: Int,
    val approvalMode: String,
    val networkMode: String,
    val contextGeneration: Int,
    val contextCheckpointed: Boolean,
)

internal fun mobileHarnessSummary(snapshot: JsonObject): MobileHarnessSummary? {
    val manifest = snapshot.objectValue("run_manifest") ?: snapshot
    val harness = manifest.objectValue("harness") ?: return null
    if (harness.textValue("schema") != "hashmm.agent-harness.v1") return null
    val context = harness.objectValue("context") ?: JsonObject(emptyMap())
    val capabilities = harness.objectValue("capabilities") ?: JsonObject(emptyMap())
    val trajectory = harness.objectValue("trajectory") ?: JsonObject(emptyMap())
    val eventTypes = trajectory.objectValue("event_types") ?: JsonObject(emptyMap())
    val children = harness.objectValue("children") ?: JsonObject(emptyMap())
    val terminal = harness.objectValue("terminal") ?: JsonObject(emptyMap())
    val contextLifecycle = manifest.objectValue("context_lifecycle")
    return MobileHarnessSummary(
        terminalReason = terminal.textValue("reason"),
        effectiveTools = capabilities.intValue("effective_count"),
        declaredTools = capabilities.intValue("declared_count"),
        missingExecutors = (capabilities["missing_executors"] as? JsonArray)?.size ?: 0,
        events = trajectory.intValue("event_count"),
        toolCalls = eventTypes.intValue("tool_finished"),
        compactions = eventTypes.intValue("context_compaction"),
        children = children.intValue("total"),
        childLimit = children.intValue("limit"),
        approvalMode = context.textValue("approval_mode"),
        networkMode = context.textValue("network_mode"),
        contextGeneration = contextLifecycle?.intValue("generation") ?: 0,
        contextCheckpointed = contextLifecycle?.textValue("checkpoint_id")?.isNotBlank() == true,
    )
}

internal fun mobileRetrievalSummary(snapshot: JsonObject): MobileRetrievalSummary? {
    val run = snapshot.objectValue("run_manifest")
        ?.objectValue("retrieval")
        ?.objectValue("run")
        ?: return null
    if (run.textValue("schema") != "hashmm.retrieval-run.v1") return null
    val status = when (run.textValue("status")) {
        "completed" -> "已完成"
        "empty" -> "没有取得证据"
        "degraded" -> "降级完成"
        "failed" -> "检索失败"
        "skipped" -> "本轮未检索"
        else -> "状态未记录"
    }
    val graph = (run["expansions"] as? JsonArray)
        ?.mapNotNull { it as? JsonObject }
        ?.firstOrNull { it.textValue("stage") == "knowledge_graph" }
    return MobileRetrievalSummary(
        statusLabel = status,
        route = run.textValue("resolved_mode"),
        evidenceCount = run.intValue("evidence_count"),
        totalCandidates = run.intValue("total_candidates"),
        attempts = (run["attempts"] as? JsonArray)?.size ?: 0,
        degradations = (run["degradations"] as? JsonArray)?.size ?: 0,
        elapsedMs = run.intValue("elapsed_ms"),
        aclScoped = run.boolValue("acl_scoped") == true,
        graphAdded = graph?.intValue("added") ?: 0,
        graphSkipped = (graph?.intValue("skipped_missing") ?: 0) +
            (graph?.intValue("skipped_forbidden") ?: 0),
    )
}

internal fun runTone(status: String): KitTone = workRunTone(status.lowercase())

internal fun runStatusLabel(status: String): String = when (status.lowercase()) {
    "checks_passed" -> "检查通过"
    "delivered_with_limits" -> "有限交付"
    "needs_attention" -> "需要处理"
    else -> mobileWorkStatus(status.lowercase())
}

internal fun formatRunDuration(ms: Long): String = when {
    ms <= 0 -> "未记录"
    ms < 1000 -> "${ms} ms"
    ms < 60_000 -> String.format("%.1f 秒", ms / 1000.0)
    else -> "${ms / 60_000} 分 ${ms % 60_000 / 1000} 秒"
}
