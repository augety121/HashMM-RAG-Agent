package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.FactCheck
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import com.hashmm.app.data.remote.MobileCompletionReceipt
import com.hashmm.app.data.remote.MobileExecutionDevice
import com.hashmm.app.data.remote.MobileGovernedNextAction
import com.hashmm.app.data.remote.MobileWorkCanvas
import com.hashmm.app.data.remote.MobileWorkCanvasResult
import com.hashmm.app.ui.chat.InAppFileViewer
import kotlinx.coroutines.launch

/**
 * A native user-facing projection of one durable work run.
 *
 * The screen deliberately does not reproduce the desktop diagnostics page.
 * It presents the same server-owned contract as four decisions a user needs:
 * what is being done, what actually happened, what supports it, and what was
 * delivered. All controls remain revision bound; suggestions never execute
 * by themselves.
 */
@Composable
fun WorkCanvasScreen(
    runId: String,
    onBack: () -> Unit,
    onOpenConversation: (String) -> Unit,
    viewModel: NativeFeedViewModel = hiltViewModel(),
) {
    val scope = rememberCoroutineScope()
    var canvas by remember(runId) { mutableStateOf<MobileWorkCanvas?>(null) }
    var loading by remember(runId) { mutableStateOf(true) }
    var busy by remember(runId) { mutableStateOf("") }
    var error by remember(runId) { mutableStateOf("") }
    var tab by remember(runId) { mutableIntStateOf(0) }
    var changeDialog by remember { mutableStateOf(false) }
    var changeNote by remember { mutableStateOf("") }
    var annotationResult by remember { mutableStateOf<MobileWorkCanvasResult?>(null) }
    var annotationNote by remember { mutableStateOf("") }
    var viewerUrl by remember { mutableStateOf<String?>(null) }
    var devices by remember(runId) { mutableStateOf<List<MobileExecutionDevice>>(emptyList()) }
    var deviceDialog by remember(runId) { mutableStateOf(false) }

    fun refresh(force: Boolean = false) {
        scope.launch {
            if (canvas == null) loading = true
            val result = viewModel.workCanvasResult(runId, force)
            val next = result.canvas
            if (next == null) {
                if (canvas == null) error = result.error ?: "这项工作当前不可用"
            } else {
                canvas = next
                error = ""
            }
            loading = false
        }
    }

    fun control(action: String) {
        val current = canvas ?: return
        if (busy.isNotBlank()) return
        busy = action
        scope.launch {
            val outcome = viewModel.workRunCommand(
                current.runId, action, current.sync.revision,
            )
            if (!outcome.ok) error = outcome.error ?: "工作控制没有执行"
            val next = viewModel.workCanvas(current.runId, force = true)
            if (next != null) canvas = next
            busy = ""
        }
    }

    fun decide(action: String, note: String = "") {
        val current = canvas ?: return
        if (busy.isNotBlank()) return
        busy = action
        scope.launch {
            val outcome = viewModel.workRunDecision(
                current.runId, action, current.sync.revision, note,
            )
            if (outcome.ok) {
                changeDialog = false
                changeNote = ""
                error = ""
            } else {
                error = outcome.error ?: "验收状态没有保存"
            }
            val next = viewModel.workCanvas(current.runId, force = true)
            if (next != null) canvas = next
            busy = ""
        }
    }

    fun openResult(result: MobileWorkCanvasResult) {
        if (result.downloadUrl.isBlank()) {
            val conv = canvas?.conversationId.orEmpty()
            if (conv.isNotBlank()) onOpenConversation(conv)
            else error = "这项成果没有可打开的文件地址"
            return
        }
        busy = "open:${result.id}"
        scope.launch {
            val url = viewModel.workResultViewUrl(result.downloadUrl, result.name)
            if (url.isNullOrBlank()) {
                error = "文件查看地址不可用，请刷新后重试"
            } else {
                viewerUrl = url
                error = ""
            }
            busy = ""
        }
    }

    fun annotate() {
        val current = canvas ?: return
        val result = annotationResult ?: return
        if (annotationNote.trim().length < 2 || busy.isNotBlank()) return
        busy = "annotation"
        scope.launch {
            val outcome = viewModel.workRunAnnotation(
                current.runId,
                result.id,
                result.version,
                annotationNote.trim(),
                current.sync.revision,
            )
            if (outcome.ok) {
                annotationResult = null
                annotationNote = ""
                error = ""
            } else {
                error = outcome.error ?: "成果批注没有保存"
            }
            val next = viewModel.workCanvas(current.runId, force = true)
            if (next != null) canvas = next
            busy = ""
        }
    }

    fun openDevicePicker() {
        if (busy.isNotBlank()) return
        busy = "devices"
        scope.launch {
            devices = viewModel.executionDevices()
            deviceDialog = true
            if (devices.isEmpty()) {
                error = "当前没有在线电脑。请在同一账号的桌面端保持登录并等待连接。"
            } else {
                error = ""
            }
            busy = ""
        }
    }

    fun chooseDevice(device: MobileExecutionDevice) {
        val current = canvas ?: return
        if (busy.isNotBlank()) return
        busy = "device:${device.deviceId}"
        scope.launch {
            val outcome = viewModel.requestWorkPlacement(
                current.runId, device.deviceId, current.sync.revision,
            )
            if (outcome.ok) {
                deviceDialog = false
                error = ""
            } else {
                error = outcome.error ?: "电脑接力没有保存"
            }
            val next = viewModel.workCanvas(current.runId, force = true)
            if (next != null) canvas = next
            busy = ""
        }
    }

    fun publishWorkflow() {
        val current = canvas ?: return
        val workflow = current.product?.workflowCandidate ?: return
        if (!workflow.canPublish || workflow.id.isBlank() || workflow.revision < 1) return
        if (busy.isNotBlank()) return
        busy = "workflow-publish"
        scope.launch {
            val outcome = viewModel.publishWorkflow(workflow.id, workflow.revision)
            if (outcome.ok) {
                error = ""
            } else {
                error = outcome.error ?: "工作流没有发布"
            }
            val next = viewModel.workCanvas(current.runId, force = true)
            if (next != null) canvas = next
            busy = ""
        }
    }

    LaunchedEffect(runId) { refresh() }

    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        ModuleHeader(
            Icons.Outlined.Hub,
            canvas?.overview?.title?.ifBlank { "工作详情" } ?: "工作详情",
            canvas?.overview?.currentStep?.ifBlank { "目标、过程、依据与成果" }
                ?: "目标、过程、依据与成果",
            onBack,
            trailing = {
                IconButton(onClick = { refresh(force = true) }, enabled = !loading) {
                    if (loading) {
                        CircularProgressIndicator(Modifier.size(17.dp), strokeWidth = 2.dp)
                    } else {
                        Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
                    }
                }
            },
        )
        when {
            loading && canvas == null -> {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
                }
            }
            canvas == null -> {
                Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            Icons.Outlined.WarningAmber, null,
                            tint = MaterialTheme.colorScheme.error,
                            modifier = Modifier.size(26.dp),
                        )
                        Spacer(Modifier.height(10.dp))
                        Text(error.ifBlank { "工作画布暂时不可用" }, fontSize = 13.sp)
                        Spacer(Modifier.height(12.dp))
                        Button(onClick = { refresh(force = true) }) { Text("重新读取") }
                    }
                }
            }
            else -> {
                val current = requireNotNull(canvas)
                WorkCanvasHeader(
                    current,
                    deviceBusy = busy == "devices" || busy.startsWith("device:"),
                    onChooseDevice = ::openDevicePicker,
                )
                WorkCanvasTabs(selected = tab, onSelect = { tab = it })
                if (error.isNotBlank()) {
                    Surface(
                        color = MaterialTheme.colorScheme.error.copy(alpha = 0.08f),
                        shape = RoundedCornerShape(13.dp),
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 7.dp),
                    ) {
                        Text(
                            error,
                            Modifier.padding(horizontal = 13.dp, vertical = 10.dp),
                            fontSize = 11.5.sp,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
                Column(
                    Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                        .padding(horizontal = 16.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    when (tab) {
                        0 -> WorkOverviewTab(
                            current,
                            busy = busy,
                            onControl = ::control,
                            onShowResults = { tab = 3 },
                            onPublishWorkflow = ::publishWorkflow,
                        )
                        1 -> WorkProcessTab(current, busy = busy, onControl = ::control)
                        2 -> WorkEvidenceTab(current)
                        else -> WorkResultsTab(
                            current,
                            busy = busy,
                            onOpenResult = ::openResult,
                            onAnnotate = { result ->
                                annotationResult = result
                                annotationNote = ""
                            },
                            onAccept = { decide("accept_delivery") },
                            onRequestChanges = { changeDialog = true },
                            onControl = ::control,
                            onOpenConversation = {
                                if (current.conversationId.isNotBlank()) {
                                    onOpenConversation(current.conversationId)
                                }
                            },
                        )
                    }
                    Spacer(Modifier.height(18.dp))
                }
            }
        }
    }

    if (changeDialog) {
        AlertDialog(
            onDismissRequest = { if (busy.isBlank()) changeDialog = false },
            title = { Text("需要修改什么") },
            text = {
                OutlinedTextField(
                    value = changeNote,
                    onValueChange = { changeNote = it.take(800) },
                    minLines = 3,
                    label = { Text("具体说明") },
                    supportingText = { Text("修改要求会回写到原工作，不会新建演示任务") },
                )
            },
            confirmButton = {
                TextButton(
                    onClick = { decide("request_changes", changeNote.trim()) },
                    enabled = changeNote.trim().length >= 2 && busy.isBlank(),
                ) { Text("提交修改") }
            },
            dismissButton = {
                TextButton(onClick = { changeDialog = false }, enabled = busy.isBlank()) {
                    Text("取消")
                }
            },
        )
    }
    annotationResult?.let { result ->
        AlertDialog(
            onDismissRequest = { if (busy.isBlank()) annotationResult = null },
            title = { Text("修改“${result.name}”") },
            text = {
                OutlinedTextField(
                    value = annotationNote,
                    onValueChange = { annotationNote = it.take(800) },
                    minLines = 3,
                    label = { Text("对第 ${result.version} 版的修改要求") },
                    supportingText = {
                        Text("只标记这个成果版本待重验；断网时会保存并使用同一请求标识补交")
                    },
                )
            },
            confirmButton = {
                TextButton(
                    onClick = ::annotate,
                    enabled = annotationNote.trim().length >= 2 && busy.isBlank(),
                ) { Text("保存批注") }
            },
            dismissButton = {
                TextButton(
                    onClick = { annotationResult = null },
                    enabled = busy.isBlank(),
                ) { Text("取消") }
            },
        )
    }
    if (deviceDialog) {
        AlertDialog(
            onDismissRequest = { if (busy.isBlank()) deviceDialog = false },
            title = { Text("选择继续工作的电脑") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "这里只保存你的选择。电脑取得短时执行凭证后才会开始操作。",
                        fontSize = 11.5.sp,
                        lineHeight = 17.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (devices.isEmpty()) {
                        Text(
                            "没有发现当前账号的在线电脑。",
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else {
                        devices.forEach { device ->
                            Surface(
                                onClick = { chooseDevice(device) },
                                enabled = busy.isBlank(),
                                shape = RoundedCornerShape(13.dp),
                                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Row(
                                    Modifier.padding(horizontal = 12.dp, vertical = 11.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Icon(
                                        Icons.Outlined.Computer,
                                        contentDescription = null,
                                        modifier = Modifier.size(19.dp),
                                    )
                                    Spacer(Modifier.width(10.dp))
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            device.name.ifBlank { "在线电脑" },
                                            fontSize = 12.5.sp,
                                            fontWeight = FontWeight.SemiBold,
                                        )
                                        Text(
                                            listOf(device.runner, device.version)
                                                .filter { it.isNotBlank() }.joinToString(" · ")
                                                .ifBlank { "桌面端已连接" },
                                            fontSize = 10.5.sp,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        )
                                    }
                                    Text(
                                        "在线",
                                        fontSize = 10.5.sp,
                                        color = Color(0xFF15803D),
                                    )
                                }
                            }
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(
                    onClick = ::openDevicePicker,
                    enabled = busy.isBlank(),
                ) { Text("刷新") }
            },
            dismissButton = {
                TextButton(
                    onClick = { deviceDialog = false },
                    enabled = busy.isBlank(),
                ) { Text("取消") }
            },
        )
    }
    viewerUrl?.let { url -> InAppFileViewer(url = url, onClose = { viewerUrl = null }) }
}

@Composable
private fun WorkCanvasHeader(
    canvas: MobileWorkCanvas,
    deviceBusy: Boolean,
    onChooseDevice: () -> Unit,
) {
    val receipt = canvas.completionReceipt
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(18.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
    ) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    canvas.overview.category.ifBlank { "工作" },
                    fontSize = 10.5.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.width(8.dp))
                StatusBadge(
                    canvas.overview.statusLabel.ifBlank { receiptStatusLabel(receipt.status) },
                    receiptTone(receipt),
                )
                Spacer(Modifier.weight(1f))
                Text(
                    kitAgo(canvas.sync.updatedAt.toLong()),
                    fontSize = 10.5.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(9.dp))
            Text(
                canvas.overview.goal.ifBlank { canvas.overview.title },
                fontSize = 15.sp,
                lineHeight = 21.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                "下一步：${canvas.overview.nextAction.ifBlank { "等待工作状态更新" }}",
                fontSize = 11.5.sp,
                lineHeight = 16.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            canvas.product?.let { product ->
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                    HeaderFact(
                        "位置",
                        product.placement.label,
                        Modifier.weight(1f),
                    )
                    HeaderFact(
                        "方式",
                        canvas.operating.methodLabel.ifBlank { product.capabilityPlan.label },
                        Modifier.weight(1f),
                    )
                    HeaderFact(
                        "可完成范围",
                        product.autonomy.label,
                        Modifier.weight(1f),
                    )
                }
                if (
                    product.placement.state == "waiting"
                    || product.placement.kind == "waiting_device"
                ) {
                    Spacer(Modifier.height(10.dp))
                    OutlinedButton(
                        onClick = onChooseDevice,
                        enabled = !deviceBusy,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(
                            Icons.Outlined.Computer,
                            contentDescription = null,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.width(7.dp))
                        Text(if (deviceBusy) "正在读取在线电脑…" else "选择一台电脑继续")
                    }
                }
            }
            if (canvas.sync.invalidatedResultIds.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    "资料发生变化，${canvas.sync.invalidatedResultIds.size} 项成果需要重新核验",
                    fontSize = 11.sp,
                    color = Color(0xFFB45309),
                )
            }
        }
    }
}

@Composable
private fun HeaderFact(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(11.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
    ) {
        Column(Modifier.padding(horizontal = 9.dp, vertical = 8.dp)) {
            Text(label, fontSize = 9.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                value,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                fontSize = 10.5.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
    }
}

@Composable
private fun WorkCanvasTabs(selected: Int, onSelect: (Int) -> Unit) {
    val labels = listOf("概览", "过程", "依据", "成果")
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(13.dp))
            .padding(3.dp),
    ) {
        labels.forEachIndexed { index, label ->
            Surface(
                color = if (selected == index) MaterialTheme.colorScheme.surface else Color.Transparent,
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.weight(1f).clickable { onSelect(index) },
            ) {
                Text(
                    label,
                    Modifier.padding(vertical = 8.dp),
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    fontSize = 11.5.sp,
                    fontWeight = if (selected == index) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (selected == index) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun WorkOverviewTab(
    canvas: MobileWorkCanvas,
    busy: String,
    onControl: (String) -> Unit,
    onShowResults: () -> Unit,
    onPublishWorkflow: () -> Unit,
) {
    canvas.product?.let { product ->
        WorkCanvasSection("工作地图", "目标、条件、步骤、依据和成果来自同一份持久工作记录") {
            Text(
                product.contract.goal.ifBlank { canvas.overview.goal },
                fontSize = 13.5.sp,
                lineHeight = 19.sp,
                fontWeight = FontWeight.SemiBold,
            )
            if (product.contract.constraints.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "约束：${product.contract.constraints.take(3).joinToString("；")}",
                    fontSize = 10.5.sp,
                    lineHeight = 15.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                AssuranceMetric("条件", product.workTwin.summary.criteria.toString(), Modifier.weight(1f))
                AssuranceMetric("步骤", product.workTwin.summary.tasks.toString(), Modifier.weight(1f))
                AssuranceMetric("依据", product.workTwin.summary.evidence.toString(), Modifier.weight(1f))
                AssuranceMetric("成果", product.workTwin.summary.artifacts.toString(), Modifier.weight(1f))
            }
        }
        WorkCanvasSection("协作与接管", "并行协作只有在收益与写入隔离通过后才会启用") {
            Text(
                product.collaboration.userSummary.ifBlank { "本次工作由一个执行链完成" },
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(5.dp))
            Text(
                if (product.collaboration.writeIsolation.status == "verified")
                    "协作写入隔离已验证；最终合并仍由整合角色完成。"
                else "协作写入隔离尚未证明，不会把并行结果直接写入正式成果。",
                fontSize = 10.5.sp,
                lineHeight = 16.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(5.dp))
            Text(
                product.workflowCandidate.requiredNextStep,
                fontSize = 10.5.sp,
                lineHeight = 16.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (product.workflowCandidate.canPublish) {
                Spacer(Modifier.height(9.dp))
                OutlinedButton(
                    onClick = onPublishWorkflow,
                    enabled = busy.isBlank(),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        if (busy == "workflow-publish") "正在发布…"
                        else "确认并发布工作流",
                    )
                }
            }
        }
    }
    WorkCanvasSection("交付准备度", "由运行事实确定，不使用模型自评代替证据") {
        Text(
            canvas.assurance.userSummary.headline.ifBlank { "正在整理可验证的工作状态" },
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            canvas.assurance.userSummary.detail.ifBlank { "服务端尚未返回完整的交付检查结果。" },
            fontSize = 11.5.sp,
            lineHeight = 17.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(10.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            AssuranceMetric(
                label = "恢复点",
                value = canvas.assurance.recovery.checkpointCount.toString(),
                modifier = Modifier.weight(1f),
            )
            AssuranceMetric(
                label = "验收通过",
                value = "${canvas.assurance.evidence.passed}/${canvas.assurance.evidence.required}",
                modifier = Modifier.weight(1f),
            )
            AssuranceMetric(
                label = "阻断项",
                value = canvas.assurance.delivery.blockers.size.toString(),
                modifier = Modifier.weight(1f),
            )
        }
    }
    if (canvas.changeImpact.requested) {
        WorkCanvasSection("修改影响", "由你的修改决定和工作依赖确定，不由模型猜测") {
            Text(
                canvas.changeImpact.reason.ifBlank { "已提出修改要求" },
                fontSize = 13.sp,
                lineHeight = 18.sp,
                fontWeight = FontWeight.Medium,
            )
            Spacer(Modifier.height(7.dp))
            Text(
                "${canvas.changeImpact.impactedStageIds.size} 个步骤、${canvas.changeImpact.impactedResultIds.size} 项成果需要继续处理",
                fontSize = 11.sp,
                color = Color(0xFFB45309),
            )
            Spacer(Modifier.height(4.dp))
            Text(
                canvas.changeImpact.nextAction,
                fontSize = 10.5.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    WorkCanvasSection("现在做到这里", "来自真实运行状态，不使用模型自报进度") {
        Text(
            canvas.overview.currentStep.ifBlank { "等待运行记录" },
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            canvas.overview.progress.label.ifBlank { "当前阶段" },
            fontSize = 11.5.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    WorkCanvasSection("工作计划", "目标变化后，受影响的依据和成果会单独标记") {
        canvas.process.stages.forEachIndexed { index, stage ->
            if (index > 0) Spacer(Modifier.height(10.dp))
            LabeledStateRow(
                number = index + 1,
                title = stage.label,
                state = stageStatusLabel(stage.status),
                tone = stageTone(stage.status),
            )
        }
    }
    WorkCanvasSection("建议的下一步", "所有建议都需要确认，不会自动执行或扩大权限") {
        if (canvas.nextActions.items.isEmpty()) {
            EmptyLine("当前没有需要额外决定的动作")
        } else {
            canvas.nextActions.items.take(6).forEachIndexed { index, item ->
                if (index > 0) Spacer(Modifier.height(10.dp))
                NextActionItem(item, busy, onControl, onShowResults)
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            canvas.nextActions.limitation,
            fontSize = 10.5.sp,
            lineHeight = 15.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    CompletionSummary(canvas.completionReceipt)
}

@Composable
private fun AssuranceMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 9.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                value,
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                label,
                fontSize = 9.5.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun WorkProcessTab(
    canvas: MobileWorkCanvas,
    busy: String,
    onControl: (String) -> Unit,
) {
    WorkCanvasSection("执行过程", "${canvas.process.eventCount} 条持久工作记录") {
        if (canvas.process.latestEvents.isEmpty()) {
            EmptyLine("还没有可展示的执行记录")
        } else {
            canvas.process.latestEvents.asReversed().take(30).forEachIndexed { index, event ->
                if (index > 0) Spacer(Modifier.height(10.dp))
                TimelineRow(
                    event.summary.ifBlank { "工作状态发生变化" },
                    listOf(event.type, kitAgo(event.createdAt.toLong()))
                        .filter { it.isNotBlank() }.joinToString(" · "),
                    stageTone(event.status),
                )
            }
        }
    }
    canvas.product?.let { product ->
        WorkCanvasSection("恢复中心", "对话、文件与执行分别恢复，不把模型回复当成恢复点") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssuranceMetric(
                    "对话",
                    if (product.recovery.conversation.available) "${product.recovery.conversation.checkpointCount} 个" else "无记录",
                    Modifier.weight(1f),
                )
                AssuranceMetric(
                    "文件",
                    if (product.recovery.files.available) "${product.recovery.files.previousVersions} 个旧版" else "无旧版",
                    Modifier.weight(1f),
                )
                AssuranceMetric(
                    "执行",
                    if (product.recovery.execution.available) "${product.recovery.execution.checkpointCount} 个" else "无记录",
                    Modifier.weight(1f),
                )
            }
        }
    }
    WorkCanvasSection("恢复点明细", "只展示运行时实际保存的恢复状态") {
        if (canvas.process.checkpoints.isEmpty()) {
            EmptyLine("当前工作没有留下恢复点")
        } else {
            canvas.process.checkpoints.forEachIndexed { index, checkpoint ->
                if (index > 0) Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Outlined.History, null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text(checkpoint.label, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
                        Text(
                            "${if (checkpoint.kind == "context") "上下文" else "执行"}恢复点 · ${kitAgo(checkpoint.createdAt.toLong())}",
                            fontSize = 10.5.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (checkpoint.canResume) {
                        TextButton(
                            onClick = { onControl("resume") },
                            enabled = busy.isBlank(),
                        ) { Text("继续") }
                    }
                }
            }
        }
    }
    WorkCanvasSection("并行协作", "每个分支仍受原任务权限、预算与审批约束") {
        if (canvas.process.branches.isEmpty()) {
            EmptyLine("本次工作没有启用并行协作")
        } else {
            canvas.process.branches.forEachIndexed { index, branch ->
                if (index > 0) Spacer(Modifier.height(9.dp))
                LabeledStateRow(
                    number = index + 1,
                    title = branch.label,
                    state = stageStatusLabel(branch.status),
                    tone = stageTone(branch.status),
                )
            }
        }
    }
}

@Composable
private fun WorkEvidenceTab(canvas: MobileWorkCanvas) {
    val summary = canvas.evidence.summary
    WorkCanvasSection("依据概览", "回答文字不是完成证据，来源、检查和执行凭证分别记录") {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MiniMetric("来源", summary.sources.toString(), Modifier.weight(1f))
            MiniMetric("检查", summary.checks.toString(), Modifier.weight(1f))
            MiniMetric("操作凭证", summary.receipts.toString(), Modifier.weight(1f))
        }
        if (summary.staleNodes > 0 || summary.invalidReceipts > 0) {
            Spacer(Modifier.height(10.dp))
            Text(
                "${summary.staleNodes} 个节点已失效，${summary.invalidReceipts} 条操作凭证无效",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
    WorkCanvasSection("资料来源", "只展示服务端持久化的来源节点") {
        if (canvas.evidence.sources.isEmpty()) {
            EmptyLine("本次工作没有可展示的来源节点")
        } else {
            canvas.evidence.sources.take(60).forEachIndexed { index, source ->
                if (index > 0) Spacer(Modifier.height(9.dp))
                TimelineRow(
                    source.label,
                    listOf(source.kind, source.trust, "版本 ${source.revision}")
                        .filter { it.isNotBlank() }.joinToString(" · "),
                    stageTone(source.status),
                )
            }
        }
    }
    WorkCanvasSection("完成条件检查", "通过、失败和无法判断是三种不同结果") {
        if (canvas.evidence.checks.isEmpty()) {
            EmptyLine("没有可观察的完成条件")
        } else {
            canvas.evidence.checks.forEachIndexed { index, check ->
                if (index > 0) Spacer(Modifier.height(9.dp))
                TimelineRow(
                    check.label,
                    check.detail,
                    stageTone(check.status),
                )
            }
        }
    }
    WorkCanvasSection("真实操作凭证", "不保存原始凭据或完整工具正文") {
        if (canvas.evidence.executionReceipts.isEmpty()) {
            EmptyLine("本次工作没有外部操作凭证")
        } else {
            canvas.evidence.executionReceipts.take(60).forEachIndexed { index, receipt ->
                if (index > 0) Spacer(Modifier.height(9.dp))
                TimelineRow(
                    receipt.tool,
                    "${receipt.sideEffect.ifBlank { "影响范围未记录" }} · ${if (receipt.reversible) "可恢复" else "不可自动恢复"}",
                    if (receipt.valid && receipt.success) KitTone.Success else KitTone.Error,
                )
            }
        }
    }
    if (canvas.learning.usedVersions.isNotEmpty()) {
        WorkCanvasSection("本次使用的技能", "只展示实际使用的已发布版本，不会自动晋升候选") {
            canvas.learning.usedVersions.forEachIndexed { index, skill ->
                if (index > 0) Spacer(Modifier.height(9.dp))
                TimelineRow(
                    skill.name.ifBlank { skill.id },
                    listOf(skill.scope, skill.versionRef)
                        .filter { it.isNotBlank() }.joinToString(" · "),
                    KitTone.Success,
                )
            }
            if (canvas.learning.limitation.isNotBlank()) {
                Spacer(Modifier.height(10.dp))
                Text(
                    canvas.learning.limitation,
                    fontSize = 11.sp,
                    lineHeight = 17.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun WorkResultsTab(
    canvas: MobileWorkCanvas,
    busy: String,
    onOpenResult: (MobileWorkCanvasResult) -> Unit,
    onAnnotate: (MobileWorkCanvasResult) -> Unit,
    onAccept: () -> Unit,
    onRequestChanges: () -> Unit,
    onControl: (String) -> Unit,
    onOpenConversation: () -> Unit,
) {
    WorkCanvasSection("交付成果", "同一成果的版本和核验状态跨端一致") {
        if (canvas.results.isEmpty()) {
            EmptyLine("尚未生成可交付成果")
        } else {
            canvas.results.forEachIndexed { index, result ->
                if (index > 0) Spacer(Modifier.height(10.dp))
                ResultRow(
                    result,
                    busy = busy == "open:${result.id}",
                    onClick = { onOpenResult(result) },
                )
                TextButton(
                    onClick = { onAnnotate(result) },
                    enabled = busy.isBlank(),
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("给这个版本提修改") }
            }
        }
        if (canvas.product?.annotations?.isNotEmpty() == true) {
            Spacer(Modifier.height(12.dp))
            Text(
                "已保存的修改要求",
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            canvas.product.annotations.takeLast(4).asReversed().forEach { annotation ->
                Spacer(Modifier.height(5.dp))
                Text(
                    "第 ${annotation.artifactRevision} 版 · ${annotation.note}",
                    fontSize = 10.5.sp,
                    lineHeight = 15.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
    CompletionReceiptCard(canvas.completionReceipt)
    val receipt = canvas.completionReceipt
    if (receipt.status == "awaiting_review") {
        WorkCanvasSection("你的验收", "验收是用户权限，不会由模型替你确认") {
            Button(
                onClick = onAccept,
                enabled = busy.isBlank(),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(13.dp),
            ) {
                if (busy == "accept_delivery") {
                    CircularProgressIndicator(Modifier.size(15.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                }
                Text("确认交付")
            }
            Spacer(Modifier.height(8.dp))
            OutlinedButton(
                onClick = onRequestChanges,
                enabled = busy.isBlank(),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(13.dp),
            ) { Text("提出修改") }
        }
    }
    WorkCanvasSection("继续这项工作", "继续使用原对话、原范围和已有上下文") {
        if (receipt.recovery.canResume) {
            OutlinedButton(
                onClick = { onControl("resume") },
                enabled = busy.isBlank(),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(13.dp),
            ) { Text("从恢复点继续") }
            Spacer(Modifier.height(8.dp))
        }
        if (receipt.recovery.canRetry) {
            OutlinedButton(
                onClick = { onControl("retry") },
                enabled = busy.isBlank(),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(13.dp),
            ) { Text("保留记录并重新尝试") }
            Spacer(Modifier.height(8.dp))
        }
        if (canvas.conversationId.isNotBlank()) {
            TextButton(onClick = onOpenConversation, modifier = Modifier.fillMaxWidth()) {
                Text("回到原对话继续说明")
            }
        }
    }
}

@Composable
private fun CompletionSummary(receipt: MobileCompletionReceipt) {
    WorkCanvasSection("完成可信度", "已交付、用户接受和可验证完成不会混为一谈") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                if (receipt.canClaimVerified) Icons.Outlined.CheckCircle else Icons.Outlined.Shield,
                null,
                tint = when {
                    receipt.canClaimVerified -> Color(0xFF15803D)
                    receipt.status == "blocked" -> MaterialTheme.colorScheme.error
                    else -> MaterialTheme.colorScheme.onSurfaceVariant
                },
                modifier = Modifier.size(20.dp),
            )
            Spacer(Modifier.width(10.dp))
            Text(
                receiptStatusLabel(receipt.status),
                fontSize = 13.5.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
        Spacer(Modifier.height(11.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MiniMetric("成果", receipt.summary.results.toString(), Modifier.weight(1f))
            MiniMetric("依据", receipt.summary.sources.toString(), Modifier.weight(1f))
            MiniMetric("检查", receipt.summary.checks.toString(), Modifier.weight(1f))
        }
    }
}

@Composable
private fun CompletionReceiptCard(receipt: MobileCompletionReceipt) {
    WorkCanvasSection("完成回执", "回执来自持久状态、完成门和有效操作凭证") {
        TimelineRow("结论", receiptStatusLabel(receipt.status), receiptTone(receipt))
        Spacer(Modifier.height(9.dp))
        TimelineRow(
            "完成条件",
            receipt.verification.status.ifBlank { "尚未形成可验证结论" },
            stageTone(receipt.verification.status),
        )
        Spacer(Modifier.height(9.dp))
        TimelineRow(
            "外部操作",
            "${receipt.summary.sideEffects} 项，其中 ${receipt.summary.irreversibleSideEffects} 项不可自动恢复",
            if (receipt.summary.irreversibleSideEffects > 0) KitTone.Warn else KitTone.Neutral,
        )
        if (receipt.limitations.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text("已知边界", fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
            receipt.limitations.take(12).forEach { item ->
                Text(
                    "• $item",
                    Modifier.padding(top = 5.dp),
                    fontSize = 10.8.sp,
                    lineHeight = 15.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun WorkCanvasSection(
    title: String,
    hint: String = "",
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(18.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(horizontal = 15.dp, vertical = 14.dp)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            if (hint.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(
                    hint,
                    fontSize = 10.5.sp,
                    lineHeight = 14.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(12.dp))
            content()
        }
    }
}

@Composable
private fun LabeledStateRow(number: Int, title: String, state: String, tone: KitTone) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Surface(
            color = stateColor(tone).copy(alpha = 0.11f),
            shape = CircleShape,
            modifier = Modifier.size(28.dp),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Text(
                    number.toString(),
                    fontSize = 10.5.sp,
                    fontWeight = FontWeight.Bold,
                    color = stateColor(tone),
                )
            }
        }
        Spacer(Modifier.width(10.dp))
        Text(title, Modifier.weight(1f), fontSize = 12.5.sp, lineHeight = 17.sp)
        StatusBadge(state, tone)
    }
}

@Composable
private fun TimelineRow(title: String, sub: String, tone: KitTone) {
    Row(verticalAlignment = Alignment.Top) {
        Box(
            Modifier.padding(top = 5.dp).size(8.dp)
                .background(stateColor(tone), CircleShape),
        )
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
            if (sub.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(
                    sub,
                    fontSize = 10.5.sp,
                    lineHeight = 14.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun NextActionItem(
    item: MobileGovernedNextAction,
    busy: String,
    onControl: (String) -> Unit,
    onShowResults: () -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(
            if (item.risk == "high") Icons.Outlined.WarningAmber else Icons.AutoMirrored.Outlined.FactCheck,
            null,
            tint = stateColor(if (item.risk == "high") KitTone.Warn else KitTone.Primary),
            modifier = Modifier.size(18.dp),
        )
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(item.label, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
            Text(
                item.reason,
                fontSize = 10.5.sp,
                lineHeight = 14.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (item.kind == "review") {
            TextButton(onClick = onShowResults) { Text("查看") }
        } else if (item.kind == "control" && item.controlAction.isNotBlank()) {
            TextButton(
                onClick = { onControl(item.controlAction) },
                enabled = busy.isBlank() && item.withinScope && !item.autoExecute,
            ) { Text("确认") }
        }
    }
}

@Composable
private fun ResultRow(
    result: MobileWorkCanvasResult,
    busy: Boolean,
    onClick: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(38.dp)
                .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(11.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(Icons.Outlined.Description, null, modifier = Modifier.size(19.dp))
        }
        Spacer(Modifier.width(11.dp))
        Column(Modifier.weight(1f)) {
            Text(
                result.name,
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                "${resultKindLabel(result.kind)} · 第 ${result.version} 版",
                fontSize = 10.5.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (busy) {
            CircularProgressIndicator(Modifier.size(15.dp), strokeWidth = 2.dp)
        } else {
            StatusBadge(resultVerificationLabel(result.verification), stageTone(result.verification))
        }
    }
}

@Composable
private fun MiniMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
        modifier = modifier,
    ) {
        Column(
            Modifier.padding(vertical = 10.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(value, fontSize = 16.sp, fontWeight = FontWeight.Bold)
            Text(label, fontSize = 9.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun EmptyLine(text: String) {
    Text(
        text,
        Modifier.fillMaxWidth().padding(vertical = 8.dp),
        fontSize = 11.5.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

private fun stageStatusLabel(status: String): String = when (status) {
    "done", "completed", "passed", "verified", "ready" -> "已完成"
    "running" -> "处理中"
    "blocked", "failed", "invalid", "missing", "stale" -> "需处理"
    "skipped" -> "已跳过"
    else -> "等待"
}

private fun stageTone(status: String): KitTone = when (status) {
    "done", "completed", "passed", "verified", "ready", "success" -> KitTone.Success
    "running" -> KitTone.Primary
    "blocked", "failed", "invalid", "missing", "stale" -> KitTone.Error
    "not_evaluable", "reported", "awaiting_review" -> KitTone.Warn
    else -> KitTone.Neutral
}

private fun receiptTone(receipt: MobileCompletionReceipt): KitTone = when {
    receipt.canClaimVerified -> KitTone.Success
    receipt.status == "blocked" -> KitTone.Error
    receipt.status == "awaiting_review" || receipt.status == "accepted_with_limits" -> KitTone.Warn
    else -> KitTone.Neutral
}

private fun receiptStatusLabel(status: String): String = when (status) {
    "verified" -> "已通过可观察条件核验"
    "accepted_with_limits" -> "已由用户接受，仍保留核验边界"
    "awaiting_review" -> "等待你的最终验收"
    "completed_with_limits" -> "已经交付，仍有核验边界"
    "blocked" -> "依据已变化，需要重新核验"
    else -> "尚未达到可交付状态"
}

private fun resultKindLabel(kind: String): String = when (kind) {
    "document" -> "文档"
    "pdf" -> "PDF"
    "presentation" -> "演示文稿"
    "spreadsheet" -> "表格"
    "canvas" -> "画布"
    "image" -> "图片"
    "text" -> "文本"
    else -> "文件"
}

private fun resultVerificationLabel(status: String): String = when (status) {
    "verified" -> "已核验"
    "ready" -> "可查看"
    "stale" -> "需复核"
    "missing" -> "文件缺失"
    else -> "已记录"
}

@Composable
private fun stateColor(tone: KitTone): Color = when (tone) {
    KitTone.Primary -> MaterialTheme.colorScheme.primary
    KitTone.Success -> Color(0xFF15803D)
    KitTone.Warn -> Color(0xFFB45309)
    KitTone.Error -> MaterialTheme.colorScheme.error
    KitTone.Neutral -> MaterialTheme.colorScheme.onSurfaceVariant
}
