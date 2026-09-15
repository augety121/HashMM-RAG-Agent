package com.hashmm.app.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material.icons.outlined.Code
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.FlashOn
import androidx.compose.material.icons.outlined.Laptop
import androidx.compose.material.icons.outlined.AccountTree
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.ui.components.HashMascot
import com.hashmm.app.ui.theme.AppType
import kotlinx.coroutines.launch

private const val BLANK_CANVAS_HTML = """
<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:32px;color:#1f2937;line-height:1.65}
main{max-width:760px;margin:0 auto}h1{font-size:28px;margin:0 0 12px}p{color:#64748b}
</style></head><body><main><h1>工作画布</h1>
<p>这是当前会话的工作画布。点击编辑后，可以直接整理方案、记录结论并保存。</p>
<h2>待完善</h2><ul><li>目标与背景</li><li>关键结论</li><li>下一步行动</li></ul>
</main></body></html>
"""

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatDetailScreen(
    onBack: () -> Unit,
    onOpenAgentsStudio: (convId: String, goal: String) -> Unit = { _, _ -> },
    viewModel: ChatDetailViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val viewerUrl by viewModel.viewerUrl.collectAsStateWithLifecycle()
    // HTML 产物使用原生画布查看器，避免在普通文档 WebView 中丢失画布编辑桥。
    var canvasFile by remember { mutableStateOf<Pair<String, String>?>(null) }
    val canvasVm: com.hashmm.app.ui.canvas.CanvasViewModel = hiltViewModel()
    val canvasScope = rememberCoroutineScope()
    val ctx = LocalContext.current
    var canvasOpening by remember { mutableStateOf(false) }

    fun openBlankCanvas() {
        if (canvasOpening) return
        canvasOpening = true
        val filename = "工作画布-${java.text.SimpleDateFormat("yyyyMMdd-HHmm", java.util.Locale.ROOT).format(java.util.Date())}.html"
        canvasScope.launch {
            val (downloadUrl, error) = canvasVm.repo.createBlank(
                convId = viewModel.conversationId,
                title = "工作画布",
                filename = filename,
                content = BLANK_CANVAS_HTML,
            )
            canvasOpening = false
            if (error.isNotBlank()) {
                android.widget.Toast.makeText(ctx, error, android.widget.Toast.LENGTH_LONG).show()
            } else {
                canvasFile = filename to downloadUrl
            }
        }
    }
    // V174：「从电脑取文件」下发结果用 Toast 反馈
    val toast by viewModel.toast.collectAsStateWithLifecycle()
    LaunchedEffect(toast) {
        toast?.let {
            android.widget.Toast.makeText(ctx, it, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearToast()
        }
    }
    val listState = rememberLazyListState()
    // V272 卡顿优化：标记"首批历史是否已展示"。首屏批量渲染时为 false → 消息不做进场动画（秒显）；
    // 之后新追加的消息为 true → 正常淡入。让打开会话瞬间见内容、丝滑不卡。
    val loadedOnce = remember { mutableStateOf(false) }
    LaunchedEffect(ui.messages.isNotEmpty()) { if (ui.messages.isNotEmpty()) loadedOnce.value = true }
    LaunchedEffect(ui.messages.size) {
        if (ui.messages.isNotEmpty()) listState.animateScrollToItem(ui.messages.size - 1)
    }

    androidx.compose.runtime.CompositionLocalProvider(
        LocalFileOpener provides { filename, downloadUrl ->
            if (filename.substringAfterLast('.', "").lowercase() in setOf("html", "htm") && downloadUrl.isNotBlank()) {
                canvasFile = filename to downloadUrl
            } else {
                viewModel.openFile(downloadUrl, filename)
            }
        }
    ) {
    Scaffold(
        topBar = {
            // V244 任务落地页：电脑任务/取文件发起的会话，顶栏直接叫「电脑任务」，
            // 并在标题下挂一条米色任务横幅（品牌"工具语气"面色）——用户点完卡片能确认"这就是任务会话"。
            Column {
                TopAppBar(
                    title = {
                        val generating = ui.sending || ui.messages.any { it.status == "streaming" }
                        val base = if (viewModel.isDispatchSession) "电脑任务" else "对话"
                        // V250 执行体切换（Marvis 式）：标题下一行小字可点，弹出 自动/桌面端/手机直连
                        var execMenu by remember { mutableStateOf(false) }
                        Column {
                            Text(if (generating) "$base · 生成中…" else base, fontWeight = FontWeight.Bold)
                            Box {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    modifier = Modifier.clip(RoundedCornerShape(6.dp))
                                        .clickable { execMenu = true }.padding(vertical = 1.dp),
                                ) {
                                    Box(Modifier.size(6.dp).clip(CircleShape).background(
                                        when (ui.execMode) {
                                            "direct" -> com.hashmm.app.ui.theme.BrandRed
                                            "backend" -> Color(0xFF34C759)
                                            else -> MaterialTheme.colorScheme.onSurfaceVariant
                                        }))
                                    Spacer(Modifier.width(5.dp))
                                    Text(
                                        when (ui.execMode) {
                                            "direct" -> "手机快速回答"
                                            "backend" -> "服务器工作区"
                                            else -> "智能选择"
                                        },
                                        fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    Icon(Icons.Outlined.ExpandMore, contentDescription = "切换执行体",
                                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.size(14.dp))
                                }
                                DropdownMenu(expanded = execMenu, onDismissRequest = { execMenu = false }) {
                                    listOf(
                                        Triple("auto", "智能选择（推荐）", "优先服务器；仅在能力等价时手机兜底"),
                                        Triple("backend", "服务器工作区", "使用 RAG、项目、工具和持久化历史"),
                                        Triple("direct", "手机快速回答", "不使用服务器知识库、文件和工具"),
                                    ).forEach { (k, t, d) ->
                                        DropdownMenuItem(
                                            text = {
                                                Column {
                                                    Text(t, fontSize = 14.sp,
                                                        fontWeight = if (ui.execMode == k) FontWeight.SemiBold else FontWeight.Normal)
                                                    Text(d, fontSize = 11.sp,
                                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                                }
                                            },
                                            trailingIcon = {
                                                if (ui.execMode == k) Icon(Icons.Outlined.Check,
                                                    contentDescription = null, modifier = Modifier.size(16.dp))
                                            },
                                            onClick = { viewModel.setExecMode(k); execMenu = false },
                                        )
                                    }
                                }
                            }
                        }
                    },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
                        }
                    },
                )
                if (viewModel.isDispatchSession) {
                    Surface(color = com.hashmm.app.ui.theme.WarmBeige, modifier = Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.Computer, null,
                                tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(15.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("桌面端任务会话 · 执行结果会回传到这里",
                                fontSize = 12.sp, color = com.hashmm.app.ui.theme.OnWarmBeige,
                                fontWeight = FontWeight.Medium)
                        }
                    }
                }
                // V1700：如实显示直连轮次是否已经写入服务器工作区。
                if (ui.directMode) {
                    Surface(color = com.hashmm.app.ui.theme.WarmBeige, modifier = Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.FlashOn, null,
                                tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(15.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(if (ui.directPersisted)
                                    "手机快速回答 · 本轮已写入服务器工作区"
                                else
                                    "手机快速回答 · 暂未写入服务器，恢复连接后将继续同步",
                                fontSize = 12.sp, color = com.hashmm.app.ui.theme.OnWarmBeige,
                                fontWeight = FontWeight.Medium)
                        }
                    }
                }
                // V262 后端未启动状态卡：替代裸报错——说清现状 + 一键切「我的手机」直连重答 / 重试后端。
                if (ui.backendDown && !ui.sending) {
                    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(16.dp),
                            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
                        Column(Modifier.padding(14.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Outlined.CloudOff, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(16.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("后端未启动", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                            }
                            Spacer(Modifier.height(6.dp))
                            Text(
                                ui.error ?: if (ui.directReady)
                                    "桌面端后端未连接。可切到「我的手机」直连模型继续这条对话，或启动后端后重试。"
                                else
                                    "桌面端后端未连接，且手机直连配置还没同步（先在桌面端配好默认模型并启动一次）。",
                                fontSize = 12.sp, lineHeight = 17.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Spacer(Modifier.height(10.dp))
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                if (ui.directReady) {
                                    Button(onClick = { viewModel.switchToDirectAndRetry() }, shape = RoundedCornerShape(10.dp),
                                           contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp)) {
                                        Text("用我的手机直连重答", fontSize = 12.sp)
                                    }
                                    Spacer(Modifier.width(10.dp))
                                }
                                OutlinedButton(onClick = { viewModel.retryAfterBackendDown() }, shape = RoundedCornerShape(10.dp),
                                               contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp)) {
                                    Text("重试后端", fontSize = 12.sp)
                                }
                                Spacer(Modifier.weight(1f))
                                TextButton(onClick = { viewModel.dismissBackendDown() },
                                           contentPadding = PaddingValues(horizontal = 6.dp)) { Text("知道了", fontSize = 12.sp) }
                            }
                            Text("提示：标题下可随时切换执行体（桌面端-后端 / 我的手机）",
                                 fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = .7f),
                                 modifier = Modifier.padding(top = 6.dp))
                        }
                    }
                }
            }
        },
        bottomBar = {
            Column {
                val dispatches by viewModel.dispatches.collectAsStateWithLifecycle()
                if (dispatches.isNotEmpty()) DispatchHistoryBar(dispatches, onOpen = { r -> r.fileUrl?.let { viewModel.openFile(it, r.fileName ?: r.query) } })
                ChatInput(
                    sending = ui.sending,
                    turnSteerable = ui.turnSteerable,
                    steering = ui.steering,
                    interrupting = ui.interrupting,
                    onSend = { viewModel.send(it) },
                    onSendVoice = { viewModel.sendVoice(it) },
                    onRequestFile = { viewModel.requestFileFromDesktop(it) },
                    onOpenAgentsStudio = { goal -> onOpenAgentsStudio(viewModel.conversationId, goal) },
                    onOpenCanvas = { openBlankCanvas() },
                    canvasOpening = canvasOpening,
                    onStop = { viewModel.stopGenerating() },
                    onTranscribe = { f, cb -> viewModel.transcribeAudio(f, cb) },
                    onComputerTask = { t, k -> viewModel.requestComputerTask(t, k) },
                )
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.messages.isEmpty() -> Column(
                    Modifier.align(Alignment.Center).padding(32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(
                        if (ui.error != null) "加载失败" else "继续这个会话",
                        style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface,
                    )
                    Spacer(Modifier.height(6.dp))
                    Text(
                        ui.error ?: "下面输入就能接着和 Agent 聊（和客户端同一个会话）。",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    state = listState,
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 10.dp),
                ) {
                    items(ui.messages, key = { it.id }) { msg ->
                        MessageBubble(
                            msg,
                            animateIn = loadedOnce.value,   // V272 首批历史秒显不卡，新消息才淡入
                            awaiting = msg.id == ui.messages.lastOrNull()?.id && !ui.sending,
                            onRegenerate = if (msg.id == ui.messages.lastOrNull()?.id && msg.role == "assistant" && !ui.sending) {
                                { viewModel.regenerate() }
                            } else null,
                            onConfirmPlan = { kind, payload -> viewModel.requestComputerTask(payload, kind) },
                            onMemAction = { action, payload ->
                                when (action) {
                                    "forget" -> viewModel.requestComputerTask(payload, "mem_forget")
                                    "forget_pref" -> viewModel.requestComputerTask(payload, "mem_pref_forget")
                                    "clear_field" -> viewModel.requestComputerTask(payload, "mem_field_clear")
                                    "set_field" -> viewModel.requestComputerTask(payload, "mem_field_set")
                                    "clear" -> viewModel.requestComputerTask("clear", "mem_clear")
                                }
                            },
                            onCancelTask = { token -> viewModel.requestComputerTask(token, "task_cancel") },
                            onInputAnswer = { answer -> viewModel.send(answer) },
                            onToolApproval = { requestId, approve ->
                                viewModel.decideToolApproval(requestId, approve)
                            },
                            onFeedback = if (!ui.directMode) {
                                { messageId, rating, reason, comment ->
                                    viewModel.submitMessageFeedback(messageId, rating, reason, comment)
                                }
                            } else null,
                        )
                    }
                    if (ui.sending && listOf(ui.taskContract, ui.liveTodo, ui.liveProgress).any { it.isNotBlank() }) {
                        item(key = "live-task-status") {
                            LiveTaskStatusCard(
                                taskContract = ui.taskContract,
                                todo = ui.liveTodo,
                                progress = ui.liveProgress,
                                stepCount = ui.liveStepCount,
                            )
                        }
                    }
                }
            }
            if (ui.error != null && ui.messages.isNotEmpty()) {
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp),
                ) {
                    Text(ui.error!!, color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                }
            }
        }
    }
    viewerUrl?.let { InAppFileViewer(url = it, onClose = { viewModel.closeViewer() }) }
    canvasFile?.let { (filename, downloadUrl) ->
        com.hashmm.app.ui.canvas.CanvasScreen(
            convId = viewModel.conversationId,
            filename = filename,
            downloadUrl = downloadUrl,
            repo = canvasVm.repo,
            onClose = { canvasFile = null },
        )
    }
    }
}

private data class LiveTaskSummary(
    val goal: String = "",
    val criteria: List<String> = emptyList(),
    val progress: String = "",
    val todoDone: Int = 0,
    val todoTotal: Int = 0,
)

private fun parseLiveTaskSummary(taskContract: String, todo: String, progress: String): LiveTaskSummary {
    val contract = runCatching { org.json.JSONObject(taskContract) }.getOrNull()
    val todoObject = runCatching { org.json.JSONObject(todo) }.getOrNull()
    val progressObject = runCatching { org.json.JSONObject(progress) }.getOrNull()
    val criteriaArray = contract?.optJSONArray("success_criteria")
    val criteria = buildList {
        if (criteriaArray != null) for (index in 0 until criteriaArray.length()) {
            val item = criteriaArray.optJSONObject(index)
            val label = item?.optString("label").orEmpty().ifBlank { item?.optString("id").orEmpty() }
            if (label.isNotBlank()) add(label)
        }
    }
    val items = todoObject?.optJSONArray("items")
    var done = 0
    if (items != null) for (index in 0 until items.length()) {
        val status = items.optJSONObject(index)?.optString("status").orEmpty()
        if (status in setOf("done", "completed", "skipped")) done += 1
    }
    return LiveTaskSummary(
        goal = contract?.optString("goal").orEmpty(),
        criteria = criteria,
        progress = progressObject?.optString("msg").orEmpty()
            .ifBlank { progressObject?.optString("message").orEmpty() },
        todoDone = done,
        todoTotal = items?.length() ?: 0,
    )
}

@Composable
internal fun LiveTaskStatusCard(taskContract: String, todo: String, progress: String, stepCount: Int) {
    val summary = remember(taskContract, todo, progress) {
        parseLiveTaskSummary(taskContract, todo, progress)
    }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        shape = RoundedCornerShape(16.dp),
        border = androidx.compose.foundation.BorderStroke(
            1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.65f),
        ),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 7.dp),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(strokeWidth = 2.dp, modifier = Modifier.size(15.dp))
                Spacer(Modifier.width(8.dp))
                Text("正在完成任务", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                if (stepCount > 0) {
                    Spacer(Modifier.width(8.dp))
                    Text("已执行 $stepCount 步", fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (summary.goal.isNotBlank()) {
                Spacer(Modifier.height(7.dp))
                Text(summary.goal, fontSize = 12.sp, lineHeight = 17.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            if (summary.progress.isNotBlank() || summary.todoTotal > 0) {
                Spacer(Modifier.height(7.dp))
                Text(
                    summary.progress.ifBlank { "待办 ${summary.todoDone}/${summary.todoTotal}" },
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (summary.criteria.isNotEmpty()) {
                Spacer(Modifier.height(7.dp))
                Text(
                    "完成条件：${summary.criteria.take(3).joinToString(" · ")}",
                    fontSize = 10.5.sp,
                    lineHeight = 15.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun DispatchHistoryBar(items: List<DispatchRecord>, onOpen: (DispatchRecord) -> Unit = {}) {
    val latest = items.first()
    val (statusText, statusColor) = when {
        latest.delivered -> "结果已送达" to MaterialTheme.colorScheme.primary
        latest.status == "processing" -> "电脑正在执行" to androidx.compose.ui.graphics.Color(0xFFB45309)
        latest.status == "done" -> "执行完成" to androidx.compose.ui.graphics.Color(0xFF15803D)
        latest.status == "error" || !latest.ok -> "执行失败" to MaterialTheme.colorScheme.error
        else -> "等待电脑认领" to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Surface(color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 9.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).clip(CircleShape).background(statusColor))
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(latest.query, fontSize = 12.5.sp, fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(statusText, fontSize = 10.5.sp, color = statusColor)
                }
                if (latest.delivered && latest.fileUrl != null) {
                    TextButton(onClick = { onOpen(latest) }) { Text("打开结果", fontSize = 12.sp) }
                } else if (items.size > 1) {
                    Text("另有 ${items.size - 1} 项", fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (latest.status == "pending" || latest.status == "processing") {
                Spacer(Modifier.height(7.dp))
                LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp), strokeCap = StrokeCap.Round)
            }
        }
    }
}

@Composable
private fun ChatInput(
    sending: Boolean,
    turnSteerable: Boolean = false,
    steering: Boolean = false,
    interrupting: Boolean = false,
    onSend: (String) -> Unit,
    onSendVoice: (String) -> Unit = onSend,
    onRequestFile: (String) -> Unit,
    onOpenAgentsStudio: (String) -> Unit = {},
    onOpenCanvas: () -> Unit = {},
    canvasOpening: Boolean = false,
    onStop: () -> Unit = {},
    onTranscribe: (java.io.File, (String?) -> Unit) -> Unit = { _, cb -> cb(null) },
    onComputerTask: (String, String) -> Unit = { _, _ -> },
) {
    var text by remember { mutableStateOf("") }
    val haptic = androidx.compose.ui.platform.LocalHapticFeedback.current
    val voiceCtx = LocalContext.current

    // 语音：设备有系统识别服务 → SpeechRecognizer（端上）；没有 → 录音上传后端 /api/stt（HTTP，最稳，能穿 AutoDL 端口映射）。
    // 两条路都：实时波形 + 上滑取消 + 识别后自动发送。
    var listening by remember { mutableStateOf(false) }
    var cancelArmed by remember { mutableStateOf(false) }
    var partialText by remember { mutableStateOf("") }
    var transcribing by remember { mutableStateOf(false) }
    val rms = remember { mutableStateListOf<Float>() }
    var recorder by remember { mutableStateOf<android.media.MediaRecorder?>(null) }
    var audioFile by remember { mutableStateOf<java.io.File?>(null) }
    var hasMicPerm by remember {
        mutableStateOf(
            androidx.core.content.ContextCompat.checkSelfPermission(
                voiceCtx, android.Manifest.permission.RECORD_AUDIO
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
        )
    }
    val permLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
    ) { granted ->
        hasMicPerm = granted
        android.widget.Toast.makeText(
            voiceCtx,
            if (granted) "已授权，长按麦克风说话" else "需要录音权限才能语音输入",
            android.widget.Toast.LENGTH_SHORT
        ).show()
    }
    val speechLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == android.app.Activity.RESULT_OK) {
            val spoken = result.data?.getStringArrayListExtra(android.speech.RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
            if (!spoken.isNullOrBlank()) text = if (text.isBlank()) spoken else (text.trimEnd() + " " + spoken)
        }
    }
    // 自动发送：把（已有输入 + 识别结果）合并发出并清空输入框。
    fun autoSend(recognized: String?) {
        val r = recognized?.trim()
        partialText = ""; rms.clear()
        if (r.isNullOrBlank()) return
        val combined = if (text.isBlank()) r else (text.trimEnd() + " " + r)
        text = ""
        onSendVoice(combined)   // 阶段 D：语音先过意图编排——电脑任务自动派桌面端；追问以气泡出现在对话里，直接说答案即可
    }
    val recognizer = remember {
        if (android.speech.SpeechRecognizer.isRecognitionAvailable(voiceCtx))
            android.speech.SpeechRecognizer.createSpeechRecognizer(voiceCtx) else null
    }
    DisposableEffect(recognizer) {
        recognizer?.setRecognitionListener(object : android.speech.RecognitionListener {
            override fun onReadyForSpeech(params: android.os.Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {
                rms.add(((rmsdB + 2f) / 12f).coerceIn(0f, 1f))
                while (rms.size > 32) rms.removeAt(0)
            }
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onError(error: Int) { listening = false; partialText = ""; rms.clear() }
            override fun onResults(results: android.os.Bundle?) {
                val spoken = results
                    ?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                listening = false
                if (!cancelArmed) autoSend(spoken) else { partialText = ""; rms.clear() }
            }
            override fun onPartialResults(partialResults: android.os.Bundle?) {
                val pr = partialResults?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                if (!pr.isNullOrBlank()) partialText = pr
            }
            override fun onEvent(eventType: Int, params: android.os.Bundle?) {}
        })
        onDispose { try { recognizer?.destroy() } catch (_: Exception) {} }
    }
    fun startListening() {
        rms.clear(); partialText = ""; cancelArmed = false
        val intent = android.content.Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL, android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(android.speech.RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        }
        try { listening = true; recognizer?.startListening(intent) } catch (_: Exception) { listening = false }
    }
    fun stopListening() { try { recognizer?.stopListening() } catch (_: Exception) {} }
    fun cancelListening() { try { recognizer?.cancel() } catch (_: Exception) {}; listening = false; partialText = ""; rms.clear() }
    fun launchFallback() {
        val intent = android.content.Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL, android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
        }
        try { speechLauncher.launch(intent) }
        catch (_: Exception) { android.widget.Toast.makeText(voiceCtx, "此设备无系统语音服务；已改用录音上传，请确保电脑端后端已开启语音转文字", android.widget.Toast.LENGTH_LONG).show() }
    }
    fun startRecording() {
        try {
            val f = java.io.File(voiceCtx.cacheDir, "voice_${System.currentTimeMillis()}.m4a")
            @Suppress("DEPRECATION")
            val rec = if (android.os.Build.VERSION.SDK_INT >= 31) android.media.MediaRecorder(voiceCtx)
                      else android.media.MediaRecorder()
            rec.setAudioSource(android.media.MediaRecorder.AudioSource.MIC)
            rec.setOutputFormat(android.media.MediaRecorder.OutputFormat.MPEG_4)
            rec.setAudioEncoder(android.media.MediaRecorder.AudioEncoder.AAC)
            rec.setAudioEncodingBitRate(64000)
            rec.setAudioSamplingRate(16000)
            rec.setOutputFile(f.absolutePath)
            rec.prepare(); rec.start()
            rms.clear(); cancelArmed = false
            recorder = rec; audioFile = f; listening = true
        } catch (_: Exception) {
            recorder = null; audioFile = null; listening = false
            launchFallback()
        }
    }
    fun finishRecording(cancel: Boolean) {
        val rec = recorder; val f = audioFile
        recorder = null; audioFile = null; listening = false
        try { rec?.stop() } catch (_: Exception) {}
        try { rec?.release() } catch (_: Exception) {}
        rms.clear()
        if (cancel || f == null) { f?.delete(); return }
        transcribing = true
        onTranscribe(f) { result ->
            transcribing = false
            try { f.delete() } catch (_: Exception) {}
            if (result.isNullOrBlank()) {
                android.widget.Toast.makeText(voiceCtx, "没识别到内容。若反复失败：用最新 start-hashmm.sh 重启电脑端后端（会自动装语音转文字）", android.widget.Toast.LENGTH_LONG).show()
            } else {
                autoSend(result)
            }
        }
    }
    fun onMicPress() {
        when {
            !hasMicPerm -> permLauncher.launch(android.Manifest.permission.RECORD_AUDIO)
            recognizer != null -> { haptic.performHapticFeedback(androidx.compose.ui.hapticfeedback.HapticFeedbackType.LongPress); startListening() }
            else -> { haptic.performHapticFeedback(androidx.compose.ui.hapticfeedback.HapticFeedbackType.LongPress); startRecording() }
        }
    }
    // 录音时用麦克风音量画波形
    LaunchedEffect(recorder) {
        val rec = recorder ?: return@LaunchedEffect
        while (recorder != null) {
            val amp = try { rec.maxAmplitude } catch (_: Exception) { 0 }
            rms.add((amp / 12000f).coerceIn(0f, 1f))
            while (rms.size > 32) rms.removeAt(0)
            kotlinx.coroutines.delay(80)
        }
    }
    Surface(color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().navigationBarsPadding().imePadding()) {
            if (listening) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(2.dp), verticalAlignment = Alignment.CenterVertically) {
                        rms.forEach { v ->
                            Box(
                                Modifier.width(3.dp).height((4f + v * 22f).dp).clip(RoundedCornerShape(2.dp))
                                    .background(if (cancelArmed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)
                            )
                        }
                    }
                    Spacer(Modifier.width(8.dp))
                    Text(if (cancelArmed) "松开取消" else "上滑取消", fontSize = 11.sp,
                        color = if (cancelArmed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (partialText.isNotBlank()) {
                    Text(partialText, Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 2.dp),
                        fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // 语音：长按说话、松开识别（自动发送）；按住上滑取消
                // V217 语音修复：pointerInput 闭包里读到的 listening/recorder 是捕获时的旧值——
                // onMicPress() 刚置 true，同帧 if(listening) 仍是 false → 走 else 空等，松手不识别/要按两次。
                // 用 rememberUpdatedState 让手势循环里读到的永远是最新值；按下后无条件跟踪到抬起。
                val listeningNow = rememberUpdatedState(listening)
                val recorderNow = rememberUpdatedState(recorder)
                Box(
                    Modifier.size(44.dp).clip(CircleShape)
                        .background(if (listening) MaterialTheme.colorScheme.primary.copy(alpha = 0.15f) else Color.Transparent)
                        .pointerInput(sending, hasMicPerm, recognizer) {
                            awaitEachGesture {
                                val down = awaitFirstDown(requireUnconsumed = false)
                                if (sending) { waitForUpOrCancellation(); return@awaitEachGesture }
                                onMicPress()
                                while (true) {
                                    val event = awaitPointerEvent()
                                    val change = event.changes.firstOrNull() ?: break
                                    cancelArmed = (change.position.y - down.position.y) < -130f
                                    if (!change.pressed) break
                                }
                                if (recorderNow.value != null) finishRecording(cancelArmed)
                                else if (listeningNow.value) { if (cancelArmed) cancelListening() else stopListening() }
                                cancelArmed = false
                            }
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(Icons.Outlined.Mic, contentDescription = "长按说话",
                        tint = if (listening) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                // 电脑任务统一入口：先选执行方式并确认任务，再进入本会话的可追踪运行。
                val canDispatch = text.isNotBlank() && !sending
                var dispatchMenu by remember { mutableStateOf(false) }
                IconButton(
                    onClick = { if (!sending) dispatchMenu = true },
                    enabled = !sending,
                ) {
                    Icon(Icons.Outlined.Laptop, contentDescription = "电脑：取文件/操作",
                        tint = if (canDispatch) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                IconButton(
                    onClick = { onOpenAgentsStudio(text) },
                    enabled = !sending,
                ) {
                    Icon(
                        Icons.Outlined.AccountTree,
                        contentDescription = "多智能体协作",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(
                    onClick = onOpenCanvas,
                    enabled = !sending && !canvasOpening,
                ) {
                    if (canvasOpening) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(17.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    } else {
                        Icon(
                            Icons.Outlined.Description,
                            contentDescription = "打开画布",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                if (dispatchMenu) {
                    DispatchSheet(
                        initialText = text,
                        onDismiss = { dispatchMenu = false },
                        onSubmit = { prompt, kind ->
                            if (kind == "file") onRequestFile(prompt) else onComputerTask(prompt, kind)
                            text = ""
                            dispatchMenu = false
                        },
                    )
                }
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(24.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    BasicTextField(
                        value = text,
                        onValueChange = { text = it },
                        enabled = true,
                        textStyle = TextStyle(color = MaterialTheme.colorScheme.onSurface, fontSize = 15.sp),
                        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                        decorationBox = { inner ->
                            if (text.isEmpty()) Text(
                                if (transcribing) "识别中…" else if (listening) "聆听中…"
                                else if (sending && turnSteerable) "补充方向，发送后作用于当前任务"
                                else if (sending) "任务正在执行，可先输入下一步要求"
                                else "继续这个会话…",
                                color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 15.sp,
                            )
                            inner()
                        },
                    )
                }
                Spacer(Modifier.size(8.dp))
                val active = text.isNotBlank()
                if (sending) {
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        if (active) {
                            Box(
                                Modifier.size(44.dp).clip(CircleShape)
                                    .background(if (turnSteerable) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant)
                                    .clickable(enabled = turnSteerable && !steering) {
                                        haptic.performHapticFeedback(androidx.compose.ui.hapticfeedback.HapticFeedbackType.LongPress)
                                        onSend(text); text = ""
                                    },
                                contentAlignment = Alignment.Center,
                            ) {
                                if (steering) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                                else Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = "追加到当前任务",
                                    tint = if (turnSteerable) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        Box(
                            Modifier.size(44.dp).clip(CircleShape)
                                .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.14f))
                                .clickable(enabled = !interrupting) { onStop() },
                            contentAlignment = Alignment.Center,
                        ) {
                            if (interrupting) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                            else Icon(Icons.Outlined.Stop, contentDescription = "停止当前任务", tint = MaterialTheme.colorScheme.primary)
                        }
                    }
                } else {
                    Box(
                        Modifier.size(44.dp).clip(CircleShape)
                            .background(if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant),
                        contentAlignment = Alignment.Center,
                    ) {
                        IconButton(onClick = { if (active) { haptic.performHapticFeedback(androidx.compose.ui.hapticfeedback.HapticFeedbackType.LongPress); onSend(text); text = "" } }, enabled = active) {
                            Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = "发送", tint = if (active) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ChatInputPlaceholder() {}

/**
 * 电脑任务底部抽屉（V201）——替换原 Material 默认 DropdownMenu。
 * 视觉与 ChatHomeScreen 快捷操作 / 工作台任务卡同一套：
 * 白底 sheet、灰色分组标签、38dp 淡染图标容器、15/12sp 字级、文件类型收成 chip 组。
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun DispatchSheet(
    initialText: String,
    onDismiss: () -> Unit,
    onSubmit: (prompt: String, kind: String) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = false)
    val maxH = (LocalConfiguration.current.screenHeightDp * 0.74f).dp
    var task by remember(initialText) { mutableStateOf(initialText) }
    var kind by remember { mutableStateOf("auto") }
    val modes = listOf(
        DispatchMode("auto", Icons.Outlined.Bolt, "智能执行", "自动选择浏览器、文件或电脑工具"),
        DispatchMode("browser_use", Icons.Outlined.Language, "浏览器调研", "搜索并阅读多个页面，带来源总结"),
        DispatchMode("file", Icons.Outlined.Description, "查找文件", "按名称、类型、目录或时间范围回传"),
        DispatchMode("seq", Icons.Outlined.Tune, "多步任务", "先规划，再按步骤执行并保留状态"),
        DispatchMode("cmd", Icons.Outlined.Terminal, "只读检查", "查看系统状态，不修改文件"),
        DispatchMode("exec", Icons.Outlined.Code, "执行操作", "可能写文件或运行命令，需要谨慎确认"),
    )
    val examples = when (kind) {
        "browser_use" -> listOf("调研三款 RAG Agent 产品，给出来源和对比结论", "查询今天的 AI 行业动态并整理成要点")
        "file" -> listOf("下载目录中最近 7 天修改的 PDF 和 Word", "查找文件名包含项目方案的文件")
        "seq" -> listOf("整理下载文件夹，先列方案，移动前让我确认", "检查项目测试失败原因并形成修复清单")
        "cmd" -> listOf("查看磁盘占用和占用最多的目录", "检查当前运行的 HashMM 相关进程")
        "exec" -> listOf("在桌面创建一份任务说明文本", "运行项目测试并把报告保存到会话")
        else -> listOf("检查项目最近的错误并给出处理建议", "查找最近文件并整理成一份摘要")
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(
            Modifier.fillMaxWidth()
                .heightIn(max = maxH)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .padding(bottom = 20.dp),
        ) {
            Text("电脑任务", style = AppType.sectionTitle, color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.padding(start = 2.dp))
            Spacer(Modifier.height(3.dp))
            Text("选择执行方式，任务会在当前会话中持续显示计划、进度、审批和结果",
                fontSize = 12.sp, lineHeight = 17.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)

            Spacer(Modifier.height(14.dp))
            Surface(
                color = com.hashmm.app.ui.theme.WarmBeige,
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
                    Text("任务说明", fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                        color = com.hashmm.app.ui.theme.OnWarmBeige)
                    Spacer(Modifier.height(7.dp))
                    BasicTextField(
                        value = task,
                        onValueChange = { task = it },
                        textStyle = TextStyle(fontSize = 14.5.sp, lineHeight = 20.sp, color = MaterialTheme.colorScheme.onSurface),
                        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 58.dp),
                        decorationBox = { inner ->
                            if (task.isBlank()) Text("说清目标、范围和期望结果，例如：整理下载目录，移动前先确认",
                                fontSize = 13.sp, lineHeight = 19.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            inner()
                        },
                    )
                }
            }

            DispatchLabel("执行方式")
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                modes.forEach { mode ->
                    DispatchModeRow(mode = mode, selected = kind == mode.kind) { kind = mode.kind }
                }
            }

            DispatchLabel("可直接使用的示例")
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                examples.forEach { example -> DispatchChip(example) { task = example } }
            }

            if (kind == "exec" || kind == "seq") {
                Spacer(Modifier.height(12.dp))
                Text(
                    if (kind == "exec") "该方式可能产生写入或命令副作用。提交后仍应核对审批范围。"
                    else "多步任务会先形成计划；需要确认的步骤应暂停等待，不应静默跳过。",
                    fontSize = 11.5.sp,
                    lineHeight = 16.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Spacer(Modifier.height(16.dp))
            Button(
                onClick = { onSubmit(task.trim(), kind) },
                enabled = task.isNotBlank(),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier.fillMaxWidth().height(48.dp),
            ) { Text("在当前会话中启动", fontWeight = FontWeight.SemiBold) }
        }
    }
}

private data class DispatchMode(
    val kind: String,
    val icon: ImageVector,
    val title: String,
    val subtitle: String,
)

@Composable
private fun DispatchModeRow(mode: DispatchMode, selected: Boolean, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).clickable(onClick = onClick)
            .background(if (selected) cs.surface else Color.Transparent)
            .padding(horizontal = 10.dp, vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(36.dp).background(
                if (selected) cs.primary.copy(alpha = 0.10f) else cs.surfaceVariant,
                RoundedCornerShape(11.dp),
            ),
            contentAlignment = Alignment.Center,
        ) { Icon(mode.icon, contentDescription = null, tint = cs.onSurface, modifier = Modifier.size(18.dp)) }
        Spacer(Modifier.width(11.dp))
        Column(Modifier.weight(1f)) {
            Text(mode.title, fontSize = 14.sp, fontWeight = FontWeight.Medium, color = cs.onSurface)
            Text(mode.subtitle, fontSize = 11.5.sp, color = cs.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (selected) Icon(Icons.Outlined.Check, contentDescription = null, tint = cs.primary, modifier = Modifier.size(18.dp))
    }
}

@Composable
private fun DispatchLabel(label: String) {
    Text(
        label,
        fontSize = 12.sp,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 8.dp, top = 10.dp, bottom = 3.dp),
    )
}

@Composable
private fun DispatchRow(icon: ImageVector, title: String, subtitle: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(34.dp).clip(RoundedCornerShape(10.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant),
            contentAlignment = Alignment.Center,
        ) { Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(19.dp)) }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface)
            Text(subtitle, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun DispatchChip(label: String, onClick: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(50),
        color = MaterialTheme.colorScheme.surfaceVariant,
        modifier = Modifier.clip(RoundedCornerShape(50)).clickable(onClick = onClick),
    ) {
        Text(label, fontSize = 12.sp, lineHeight = 16.sp, color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
    }
}
