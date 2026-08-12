package com.hashmm.app.ui.studio

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.FactCheck
import androidx.compose.material.icons.automirrored.outlined.ViewQuilt
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.DocActionA
import com.hashmm.app.data.remote.DocResult
import com.hashmm.app.data.remote.StudioRepository
import com.hashmm.app.ui.components.ScreenHeader
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 文档工坊（App 原生版，V259）：文件深度理解与生成——上传/粘贴 → 动作 → 产出进画布。 */
@HiltViewModel
class DocStudioViewModel @Inject constructor(private val repo: StudioRepository) : ViewModel() {
    var actions by mutableStateOf<List<DocActionA>>(emptyList()); private set
    var actionsLoading by mutableStateOf(true); private set
    var actionsError by mutableStateOf(""); private set
    var srcFile by mutableStateOf(true)
    var fileName by mutableStateOf("")
    var fileConv by mutableStateOf("")
    var text by mutableStateOf("")
    var customIns by mutableStateOf("")
    var target by mutableStateOf("md")
    var selectedAction by mutableStateOf("")
    var busyAction by mutableStateOf(""); private set
    var uploading by mutableStateOf(false); private set
    var err by mutableStateOf("")
    var out by mutableStateOf<DocResult?>(null); private set

    init { loadActions() }

    fun loadActions() {
        actionsLoading = true
        actionsError = ""
        viewModelScope.launch {
            val result = repo.docActionsResult()
            actions = result.items
            actionsError = result.error.ifBlank {
                if (result.items.isEmpty()) "服务器当前没有可用的文档处理动作" else ""
            }
            if (selectedAction.isBlank()) selectedAction = actions.firstOrNull()?.id.orEmpty()
            actionsLoading = false
        }
    }

    fun upload(name: String, bytes: ByteArray) {
        err = ""; out = null; uploading = true
        viewModelScope.launch {
            var cid = fileConv
            if (cid.isBlank()) {
                cid = "c" + System.currentTimeMillis().toString(36)
                if (!repo.createConversation(cid, "文档工坊")) {
                    uploading = false
                    err = "创建结果会话失败，请检查登录状态和后端连接"
                    return@launch
                }
                fileConv = cid
            }
            val (fn, e) = repo.uploadFile(cid, name, bytes)
            uploading = false
            if (fn.isBlank()) { err = e.ifBlank { "上传失败" }; return@launch }
            fileName = fn; fileConv = cid
        }
    }

    fun run(action: String) {
        if (busyAction.isNotBlank()) return
        err = ""; out = null
        val isDesign = action.startsWith("design_")
        if (!isDesign && srcFile && fileName.isBlank()) { err = "先选择并上传一个文件"; return }
        if (!isDesign && !srcFile && text.isBlank()) { err = "先粘贴要处理的文本"; return }
        if ((isDesign || action == "custom") && customIns.isBlank()) {
            err = if (isDesign) "先写清要设计的内容、受众和使用场景" else "自定义指令：先写清处理要求"
            return
        }
        busyAction = action
        viewModelScope.launch {
            var cid = fileConv
            if (cid.isBlank()) {
                cid = "c" + System.currentTimeMillis().toString(36)
                if (!repo.createConversation(cid, "文档工坊")) {
                    busyAction = ""
                    err = "创建结果会话失败，请检查登录状态和后端连接"
                    return@launch
                }
                fileConv = cid
            }
            val (r, e) = repo.docRun(
                cid,
                if (!isDesign && srcFile) fileName else null,
                if (!isDesign && !srcFile) text else null,
                action,
                target,
                if (isDesign || action == "custom" || action == "data_qa") customIns.trim() else "",
            )
            busyAction = ""
            if (r == null) { err = e.ifBlank { "执行失败" }; return@launch }
            out = r
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun DocStudioScreen(
    onBack: () -> Unit,
    onOpenConversation: (String) -> Unit = {},
    vm: DocStudioViewModel = hiltViewModel(),
) {
    val cs = MaterialTheme.colorScheme
    val ctx = LocalContext.current
    val designSelected = vm.selectedAction.startsWith("design_")
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        try {
            val name = ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
                val idx = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                if (c.moveToFirst() && idx >= 0) c.getString(idx) else "文件"
            } ?: "文件"
            val bytes = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            if (bytes == null || bytes.isEmpty()) { vm.err = "读取文件失败"; return@rememberLauncherForActivityResult }
            if (bytes.size > 25 * 1024 * 1024) { vm.err = "文件过大（上限 25MB）"; return@rememberLauncherForActivityResult }
            vm.upload(name, bytes)
        } catch (_: Exception) { vm.err = "读取文件失败" }
    }

    fun iconFor(id: String) = when (id) {
        "office_audit" -> Icons.AutoMirrored.Outlined.FactCheck
        "deep_read" -> Icons.Outlined.Psychology
        "polish" -> Icons.Outlined.AutoAwesome
        "chart" -> Icons.Outlined.BarChart
        "convert" -> Icons.Outlined.SwapHoriz
        "brief" -> Icons.Outlined.Description
        "custom" -> Icons.Outlined.Terminal
        "audio_minutes" -> Icons.Outlined.Mic
        "data_qa" -> Icons.Outlined.QueryStats
        "design_icon" -> Icons.Outlined.Palette
        "design_poster" -> Icons.AutoMirrored.Outlined.ViewQuilt
        "design_slides" -> Icons.Outlined.Slideshow
        "design_infographic" -> Icons.Outlined.Insights
        else -> Icons.Outlined.Description
    }

    Scaffold(topBar = {
        ScreenHeader(
            title = "文档任务",
            subtitle = "选择资料和处理方式，结果回到同一对话",
            onBack = onBack,
        )
    }) { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).padding(horizontal = 16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Spacer(Modifier.height(2.dp))
            StudioStageBar(
                steps = listOf("选资料", "定要求", "处理", "交付"),
                activeIndex = when {
                    vm.out != null -> 3
                    vm.busyAction.isNotBlank() -> 2
                    designSelected && vm.customIns.isNotBlank() -> 1
                    vm.fileName.isNotBlank() || vm.text.isNotBlank() -> 1
                    else -> 0
                },
            )
            // 设计动作从一句需求开始；文档动作才需要上传或粘贴来源。
            if (!designSelected) StudioCard {
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    SegmentedButton(selected = vm.srcFile, onClick = { vm.srcFile = true },
                        shape = SegmentedButtonDefaults.itemShape(0, 2)) { Text("上传文件") }
                    SegmentedButton(selected = !vm.srcFile, onClick = { vm.srcFile = false },
                        shape = SegmentedButtonDefaults.itemShape(1, 2)) { Text("粘贴文本") }
                }
                Spacer(Modifier.height(10.dp))
                if (vm.srcFile) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedButton(onClick = { picker.launch("*/*") }, enabled = !vm.uploading,
                            shape = RoundedCornerShape(12.dp)) {
                            if (vm.uploading) CircularProgressIndicator(Modifier.size(15.dp), strokeWidth = 2.dp)
                            else { Icon(Icons.Outlined.UploadFile, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("选择文件") }
                        }
                        Spacer(Modifier.width(10.dp))
                        Text(
                            if (vm.fileName.isNotBlank()) vm.fileName else "pdf / docx / xlsx / 音频(mp3·wav·m4a→纪要)…",
                            fontSize = 12.sp,
                            color = if (vm.fileName.isNotBlank()) cs.primary else cs.onSurfaceVariant,
                            maxLines = 1,
                        )
                    }
                } else {
                    OutlinedTextField(
                        value = vm.text, onValueChange = { vm.text = it },
                        placeholder = { Text("把要处理的文档内容粘贴到这里…") },
                        minLines = 4, shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
            // 动作先选择、再确认执行；手机端不能把整张卡片做成易误触的立即运行按钮。
            Column(Modifier.padding(horizontal = 4.dp)) {
                Text("想怎样处理", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                Text("先选择处理方式，确认后才会开始。", fontSize = 11.5.sp, color = cs.onSurfaceVariant)
            }
            if (vm.actionsLoading) {
                com.hashmm.app.ui.components.HmmStateView(
                    kind = com.hashmm.app.ui.components.HmmStateKind.Loading,
                    message = "正在读取处理能力",
                )
            } else if (vm.actions.isEmpty()) {
                com.hashmm.app.ui.components.HmmStateView(
                    kind = com.hashmm.app.ui.components.HmmStateKind.Error,
                    title = "暂时无法读取文档能力",
                    message = vm.actionsError,
                    icon = Icons.Outlined.Description,
                    onRetry = vm::loadActions,
                )
            }
            vm.actions.chunked(2).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    row.forEach { a ->
                        val selected = vm.selectedAction == a.id
                        Surface(
                            shape = RoundedCornerShape(16.dp),
                            color = if (selected) cs.primaryContainer.copy(alpha = 0.55f) else cs.surface,
                            border = if (selected) androidx.compose.foundation.BorderStroke(1.5.dp, cs.primary) else null,
                            onClick = { vm.selectedAction = a.id },
                            enabled = vm.busyAction.isBlank(),
                            modifier = Modifier.weight(1f),
                        ) {
                            Column(Modifier.padding(12.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(iconFor(a.id), null, tint = cs.primary, modifier = Modifier.size(16.dp))
                                    Spacer(Modifier.width(7.dp))
                                    Text(a.name, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                                    Spacer(Modifier.weight(1f))
                                    if (selected) Icon(Icons.Outlined.CheckCircle, null, tint = cs.primary, modifier = Modifier.size(15.dp))
                                }
                                Spacer(Modifier.height(4.dp))
                                Text(a.desc, fontSize = 10.5.sp, color = cs.onSurfaceVariant, lineHeight = 14.sp, minLines = 2)
                                if (a.id == "convert") {
                                    Spacer(Modifier.height(6.dp))
                                    Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                                        listOf("md", "html", "txt").forEach { x ->
                                            FilterChip(selected = vm.target == x, onClick = { vm.target = x },
                                                label = { Text(x, fontSize = 10.sp) },
                                                modifier = Modifier.height(26.dp))
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if (row.size == 1) Spacer(Modifier.weight(1f))
                }
            }
            if (vm.selectedAction == "custom" || vm.selectedAction == "data_qa" || designSelected) {
                OutlinedTextField(
                    value = vm.customIns, onValueChange = { vm.customIns = it },
                    label = { Text(if (designSelected) "设计需求" else if (vm.selectedAction == "data_qa") "想问数据什么" else "具体处理要求") },
                    placeholder = { Text(if (designSelected) "例：为多智能体协作工作区设计一张面向企业用户的发布海报，克制、可信" else "例：抽取所有金额并按时间排序成表格") },
                    minLines = 2, shape = RoundedCornerShape(14.dp),
                    leadingIcon = { Icon(Icons.Outlined.Terminal, null, Modifier.size(16.dp)) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            Button(
                onClick = { vm.run(vm.selectedAction) },
                enabled = vm.selectedAction.isNotBlank() && vm.busyAction.isBlank() &&
                    (if (designSelected) vm.customIns.isNotBlank()
                     else ((vm.srcFile && vm.fileName.isNotBlank()) || (!vm.srcFile && vm.text.isNotBlank()))),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier.fillMaxWidth().height(50.dp),
            ) {
                if (vm.busyAction.isNotBlank()) {
                    CircularProgressIndicator(Modifier.size(17.dp), strokeWidth = 2.dp, color = cs.onPrimary)
                    Spacer(Modifier.width(8.dp))
                    Text("正在处理")
                } else {
                    Icon(Icons.Outlined.PlayArrow, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(7.dp))
                    Text("开始处理", fontWeight = FontWeight.SemiBold)
                }
            }
            if (vm.busyAction.isNotBlank()) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 4.dp)) {
                    CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("正在深度处理，长文档约需 20~60 秒…", fontSize = 12.sp, color = cs.onSurfaceVariant)
                }
            }
            if (vm.err.isNotBlank()) Text(vm.err, color = cs.error, fontSize = 12.5.sp, modifier = Modifier.padding(start = 4.dp))
            // 产出
            vm.out?.let { o ->
                Surface(shape = RoundedCornerShape(18.dp), color = cs.primaryContainer.copy(alpha = 0.5f)) {
                    Column(Modifier.padding(14.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.CheckCircle, null, tint = cs.primary, modifier = Modifier.size(15.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(o.note.ifBlank { "已完成" }, fontWeight = FontWeight.Bold, fontSize = 12.5.sp, color = cs.primary)
                        }
                        Spacer(Modifier.height(8.dp))
                        if (o.file.isNotBlank()) {
                            Spacer(Modifier.height(10.dp))
                            Surface(
                                shape = RoundedCornerShape(14.dp),
                                color = cs.surface,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Icon(Icons.Outlined.DashboardCustomize, null, tint = cs.primary)
                                    Spacer(Modifier.width(10.dp))
                                    Column(Modifier.weight(1f)) {
                                        Text(o.file, fontWeight = FontWeight.SemiBold, fontSize = 12.5.sp, maxLines = 1)
                                        Text("已进入同一对话的画布，可在桌面端预览、编辑、出版本和发布", fontSize = 10.5.sp, color = cs.onSurfaceVariant)
                                    }
                                }
                            }
                        } else {
                            Spacer(Modifier.height(8.dp))
                            Text(o.content, fontSize = 12.5.sp, lineHeight = 18.sp, maxLines = 40)
                        }
                        if (o.convId.isNotBlank() || vm.fileConv.isNotBlank()) {
                            Spacer(Modifier.height(10.dp))
                            Button(
                                onClick = { onOpenConversation(o.convId.ifBlank { vm.fileConv }) },
                                modifier = Modifier.fillMaxWidth(),
                                shape = RoundedCornerShape(12.dp),
                            ) { Text("在对话中查看并继续") }
                        }
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}
