package com.hashmm.app.ui.studio

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.*
import com.hashmm.app.ui.components.ScreenHeader
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 总控中枢（App 原生版，V259）：体检 / 焦点 / 模块 / 意识流 / 问工作区 / 循环工程 / 事件规则。 */
@HiltViewModel
class ControlHubViewModel @Inject constructor(private val repo: StudioRepository) : ViewModel() {
    var snap by mutableStateOf<GwSnapshotA?>(null); private set
    var release by mutableStateOf(""); private set
    var deepMode by mutableStateOf(""); private set
    var loops by mutableStateOf<List<LoopInfoA>>(emptyList()); private set
    var rules by mutableStateOf<List<EventRuleA>>(emptyList()); private set
    var offline by mutableStateOf(false); private set
    var readError by mutableStateOf(""); private set

    var question by mutableStateOf("")
    var answer by mutableStateOf(""); private set
    var answerModel by mutableStateOf(""); private set
    var asking by mutableStateOf(false); private set

    var newGoal by mutableStateOf("")
    var newAcceptance by mutableStateOf("")
    var loopApprovalMode by mutableStateOf("read_only")
    var loopNetworkMode by mutableStateOf("deny")
    var loopSubagents by mutableStateOf(false)
    var loopMsg by mutableStateOf("")
    val acceptanceNotes = mutableStateMapOf<String, String>()

    init {
        viewModelScope.launch {
            val (rel, ds) = repo.health()
            release = rel; deepMode = ds
            val ruleResult = repo.rulesResult()
            rules = ruleResult.items
            if (ruleResult.error.isNotBlank()) readError = ruleResult.error
            while (isActive) {
                refreshSnapshot()
                val active = loops.any { it.status == "running" } || snap?.focus != null
                delay(if (offline) 30_000 else if (active) 4_000 else 15_000)
            }
        }
    }

    private suspend fun refreshSnapshot() {
        val s = repo.gwState()
        if (s == null) offline = true else { offline = false; snap = s }
        val loopResult = repo.loopsResult()
        if (loopResult.error.isBlank()) {
            loops = loopResult.items
            readError = ""
        } else {
            readError = loopResult.error
        }
    }

    fun refresh() {
        viewModelScope.launch {
            val (rel, ds) = repo.health()
            release = rel; deepMode = ds
            val ruleResult = repo.rulesResult()
            if (ruleResult.error.isBlank()) rules = ruleResult.items else readError = ruleResult.error
            refreshSnapshot()
        }
    }

    fun ask() {
        val q = question.trim()
        if (q.isBlank() || asking) return
        asking = true; answer = ""
        viewModelScope.launch {
            val (a, m) = repo.gwAsk(q)
            asking = false
            answer = a.ifBlank { "回答失败：$m" }
            answerModel = m
        }
    }

    fun startGoalLoop() {
        val g = newGoal.trim()
        val acceptance = newAcceptance.trim()
        if (g.isBlank()) { loopMsg = "先写清任务目标"; return }
        if (acceptance.isBlank()) { loopMsg = "再写一条可以检查的完成标准"; return }
        viewModelScope.launch {
            val err = repo.loopGoal(
                g, acceptance, 4, loopApprovalMode, loopNetworkMode, loopSubagents,
            )
            loopMsg = if (err.isBlank()) "持续任务已启动，权限和完成标准会随任务保存" else err
            if (err.isBlank()) { newGoal = ""; newAcceptance = "" }
            val result = repo.loopsResult()
            if (result.error.isBlank()) loops = result.items else readError = result.error
        }
    }

    fun stopLoop(id: String) {
        viewModelScope.launch {
            repo.loopStop(id)
            val result = repo.loopsResult()
            if (result.error.isBlank()) loops = result.items else readError = result.error
        }
    }

    fun reviewDelivery(id: String, accepted: Boolean) {
        viewModelScope.launch {
            val error = repo.loopAcceptance(id, accepted, acceptanceNotes[id].orEmpty())
            loopMsg = if (error.isBlank()) {
                acceptanceNotes.remove(id)
                if (accepted) "交付已验收，完成状态已按证据重新计算" else "已退回复核，任务不会被标记为完成"
            } else error
            val result = repo.loopsResult()
            if (result.error.isBlank()) loops = result.items else readError = result.error
        }
    }

    fun toggleRule(id: String, on: Boolean) {
        val previous = rules
        rules = rules.map { if (it.id == id) it.copy(on = on) else it }
        viewModelScope.launch {
            if (!repo.setRule(id, on)) {
                rules = previous
                readError = "自动处理规则保存失败，请检查连接后重试"
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ControlHubScreen(onBack: () -> Unit, vm: ControlHubViewModel = hiltViewModel()) {
    val cs = MaterialTheme.colorScheme
    Scaffold(topBar = {
        ScreenHeader(
            title = "工作总览",
            subtitle = "看进度、持续完成目标并管理自动处理",
            onBack = onBack,
            actions = {
                IconButton(onClick = vm::refresh) {
                    Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
                }
            },
        )
    }) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { Spacer(Modifier.height(2.dp)) }
            item {
                StudioStageBar(
                    steps = listOf("服务", "进行中", "自动处理"),
                    activeIndex = when {
                        vm.rules.any { it.on } -> 2
                        vm.loops.any { it.status == "running" } || vm.snap?.focus != null -> 1
                        else -> 0
                    },
                )
            }
            // 手机端只给出是否可工作的结论，版本号和模块细节留给连接诊断。
            item {
                StudioCard {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.MonitorHeart, null, tint = cs.primary, modifier = Modifier.size(15.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("工作服务", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                    }
                    Text(
                        if (vm.offline) "当前无法连接服务，已显示上一次取得的状态。"
                        else "手机、服务器和任务状态会在这里保持同步。",
                        fontSize = 11.5.sp,
                        lineHeight = 16.sp,
                        color = cs.onSurfaceVariant,
                        modifier = Modifier.padding(top = 5.dp),
                    )
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        PulseChip("服务", if (vm.release.isNotBlank()) "已连接" else if (vm.offline) "离线" else "连接中", vm.release.isNotBlank())
                        PulseChip("资料检索", when (vm.deepMode) { "full" -> "完整"; "lite" -> "轻量"; else -> "未就绪" }, vm.deepMode == "full" || vm.deepMode == "lite")
                        PulseChip("任务同步", if (vm.offline) "暂停" else "正常", !vm.offline)
                    }
                }
            }
            if (vm.readError.isNotBlank()) item {
                com.hashmm.app.ui.components.HmmStateView(
                    kind = com.hashmm.app.ui.components.HmmStateKind.Error,
                    title = "工作状态未完全同步",
                    message = vm.readError,
                    icon = Icons.Outlined.CloudOff,
                    onRetry = vm::refresh,
                )
            }
            // 焦点 + 模块
            item {
                StudioCard {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.CenterFocusStrong, null, tint = cs.primary, modifier = Modifier.size(15.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("现在正在做", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                    }
                    Spacer(Modifier.height(6.dp))
                    val f = vm.snap?.focus
                    if (f != null) Text(f.goal, fontSize = 13.sp, lineHeight = 18.sp)
                    else Text("目前没有持续执行的复杂任务", fontSize = 12.5.sp, color = cs.onSurfaceVariant)
                    val active = vm.snap?.modules?.filter { it.state != "idle" } ?: emptyList()
                    if (active.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            active.forEach { m ->
                                Surface(
                                    color = cs.primaryContainer.copy(alpha = 0.55f),
                                    shape = RoundedCornerShape(999.dp),
                                ) {
                                    Text(
                                        "${m.name} · ${humanModuleState(m.state)}",
                                        fontSize = 10.5.sp,
                                        color = cs.onPrimaryContainer,
                                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                                    )
                                }
                            }
                        }
                    }
                }
            }
            // 最近进展：后端广播转换成用户可读时间线，不展示内部“意识流”术语。
            val buf = vm.snap?.buffer ?: emptyList()
            if (buf.isNotEmpty()) {
                item {
                    StudioCard {
                        Text("最近进展", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Spacer(Modifier.height(6.dp))
                        buf.take(5).forEach { b ->
                            Row(Modifier.padding(vertical = 3.dp), verticalAlignment = Alignment.Top) {
                                Box(Modifier.padding(top = 6.dp).size(5.dp).background(cs.primary, CircleShape))
                                Spacer(Modifier.width(8.dp))
                                Text("${b.module}：${b.summary}", fontSize = 12.sp, lineHeight = 17.sp, color = cs.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
            // 这是状态查询，不是第二个 Chat；回答只围绕当前工作。
            item {
                StudioCard {
                    Text("问问当前进度", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                    Text("只查询当前工作状态，不会新建另一段对话", fontSize = 11.sp, color = cs.onSurfaceVariant)
                    Spacer(Modifier.height(8.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = vm.question, onValueChange = { vm.question = it },
                            placeholder = { Text("例：哪些步骤完成了？", fontSize = 12.5.sp) },
                            singleLine = true, shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(8.dp))
                        Button(onClick = { vm.ask() }, enabled = !vm.asking, shape = RoundedCornerShape(12.dp)) {
                            if (vm.asking) CircularProgressIndicator(Modifier.size(15.dp), strokeWidth = 2.dp, color = cs.onPrimary)
                            else Text("提问")
                        }
                    }
                    if (vm.answer.isNotBlank()) {
                        Spacer(Modifier.height(8.dp))
                        Surface(shape = RoundedCornerShape(12.dp), color = cs.surface) {
                            Column(Modifier.fillMaxWidth().padding(10.dp)) {
                                if (vm.answerModel.isNotBlank())
                                    Text("由 ${vm.answerModel} 回答", fontSize = 10.sp, color = cs.onSurfaceVariant)
                                Spacer(Modifier.height(3.dp))
                                Text(vm.answer, fontSize = 12.5.sp, lineHeight = 18.sp)
                            }
                        }
                    }
                }
            }
            // 持续任务：与桌面端共用同一个持久任务契约和权限范围。
            item {
                StudioCard {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Autorenew, null, tint = cs.primary, modifier = Modifier.size(15.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("持续完成任务", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Spacer(Modifier.weight(1f))
                        Text("可暂停、恢复并自动验收", fontSize = 10.sp, color = cs.onSurfaceVariant)
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = vm.newGoal, onValueChange = { vm.newGoal = it },
                        label = { Text("任务目标") },
                        placeholder = { Text("例：核实三份资料并生成竞品报告", fontSize = 12.sp) },
                        minLines = 2, maxLines = 4, shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = vm.newAcceptance, onValueChange = { vm.newAcceptance = it },
                        label = { Text("完成标准") },
                        placeholder = { Text("例：含三个官方来源，并生成可下载文件", fontSize = 12.sp) },
                        minLines = 2, maxLines = 3, shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(8.dp))
                    Text("本次任务权限", fontSize = 11.sp, color = cs.onSurfaceVariant)
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                        modifier = Modifier.padding(top = 5.dp),
                    ) {
                        FilterChip(
                            selected = vm.loopApprovalMode == "workspace",
                            onClick = { vm.loopApprovalMode = if (vm.loopApprovalMode == "workspace") "read_only" else "workspace" },
                            label = { Text(if (vm.loopApprovalMode == "workspace") "可写工作区" else "只读", fontSize = 10.5.sp) },
                            leadingIcon = { Icon(Icons.Outlined.FolderOpen, null, Modifier.size(14.dp)) },
                        )
                        FilterChip(
                            selected = vm.loopNetworkMode == "allow",
                            onClick = { vm.loopNetworkMode = if (vm.loopNetworkMode == "allow") "deny" else "allow" },
                            label = { Text(if (vm.loopNetworkMode == "allow") "允许联网" else "不联网", fontSize = 10.5.sp) },
                            leadingIcon = { Icon(Icons.Outlined.Language, null, Modifier.size(14.dp)) },
                        )
                        FilterChip(
                            selected = vm.loopSubagents,
                            onClick = { vm.loopSubagents = !vm.loopSubagents },
                            label = { Text(if (vm.loopSubagents) "按需派专员" else "单 Agent", fontSize = 10.5.sp) },
                            leadingIcon = { Icon(Icons.Outlined.GroupWork, null, Modifier.size(14.dp)) },
                        )
                    }
                    Text(
                        "这些权限只属于本次任务；派生专员只能获得更窄的工具范围。",
                        fontSize = 10.5.sp, color = cs.onSurfaceVariant,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                    Button(
                        onClick = { vm.startGoalLoop() }, shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    ) { Text("启动持续任务") }
                    if (vm.loopMsg.isNotBlank()) {
                        Spacer(Modifier.height(4.dp))
                        Text(vm.loopMsg, fontSize = 11.sp, color = cs.onSurfaceVariant)
                    }
                    vm.loops.take(5).forEach { l ->
                        Spacer(Modifier.height(8.dp))
                        Surface(shape = RoundedCornerShape(12.dp), color = cs.surface) {
                            Column(Modifier.fillMaxWidth().padding(10.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(if (l.type == "goal") Icons.Outlined.Flag else Icons.Outlined.Schedule,
                                        null, Modifier.size(13.dp), tint = cs.onSurfaceVariant)
                                    Spacer(Modifier.width(6.dp))
                                    Text(l.label, fontSize = 12.5.sp, fontWeight = FontWeight.Medium,
                                        maxLines = 1, modifier = Modifier.weight(1f))
                                    StateDot(when (l.status) { "running" -> "run"; "done" -> "ok"; "fail" -> "fail"; else -> "wait" })
                                    if (l.status == "running") {
                                        Spacer(Modifier.width(6.dp))
                                        IconButton(onClick = { vm.stopLoop(l.id) }, modifier = Modifier.size(22.dp)) {
                                            Icon(Icons.Outlined.StopCircle, "停止", tint = cs.error, modifier = Modifier.size(16.dp))
                                        }
                                    }
                                }
                                Text(l.progress + (if (l.type == "goal") " · ${l.score} 分" else ""),
                                    fontSize = 10.5.sp, color = cs.onSurfaceVariant)
                                Text(
                                    buildString {
                                        append(if (l.networkMode == "allow") "允许联网" else "不联网")
                                        append(" · ")
                                        append(if (l.allowSubagents) "可派专员" else "单 Agent")
                                        append(" · ${l.allowedToolCount} 项工具")
                                    },
                                    fontSize = 10.sp, color = cs.onSurfaceVariant,
                                )
                                if (l.gateStatus.isNotBlank()) {
                                    Spacer(Modifier.height(4.dp))
                                    val gateOk = l.gateStatus == "verified"
                                    Surface(
                                        shape = RoundedCornerShape(9.dp),
                                        color = if (gateOk) Color(0xFF15803D).copy(alpha = 0.07f) else Color(0xFF9A6700).copy(alpha = 0.07f),
                                        border = androidx.compose.foundation.BorderStroke(1.dp,
                                            if (gateOk) Color(0xFF15803D).copy(alpha = 0.22f) else Color(0xFF9A6700).copy(alpha = 0.22f)),
                                    ) {
                                        Column(Modifier.fillMaxWidth().padding(horizontal = 9.dp, vertical = 7.dp)) {
                                            Row(verticalAlignment = Alignment.CenterVertically) {
                                                Icon(if (gateOk) Icons.Outlined.CheckCircle else Icons.Outlined.PendingActions,
                                                    null, Modifier.size(12.dp), tint = if (gateOk) Color(0xFF15803D) else Color(0xFF9A6700))
                                                Spacer(Modifier.width(5.dp))
                                                Text("完成门", fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                                                Spacer(Modifier.weight(1f))
                                                Text(if (gateOk) "已验证" else if (l.gateStatus == "delivered_with_limits") "待复核" else "未闭环",
                                                    fontSize = 9.5.sp, color = if (gateOk) Color(0xFF15803D) else Color(0xFF9A6700))
                                            }
                                            Text("${l.gatePassed}/${l.gateRequired} 必需条件通过 · 失败 ${l.gateFailed} · 待核验 ${l.gateReview}",
                                                fontSize = 9.5.sp, color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 3.dp))
                                            if (l.gateNextAction.isNotBlank()) Text(l.gateNextAction, fontSize = 9.5.sp,
                                                lineHeight = 14.sp, color = cs.onSurfaceVariant,
                                                modifier = Modifier.padding(top = 3.dp), maxLines = 2)
                                            if (l.type == "goal" && l.status == "done" && l.awaitingUserAcceptance) {
                                                Spacer(Modifier.height(7.dp))
                                                HorizontalDivider(color = cs.outlineVariant.copy(alpha = 0.55f))
                                                Text(
                                                    "按你写下的完成标准检查交付。决定会同步到桌面端并作为审计证据保存。",
                                                    fontSize = 9.5.sp, lineHeight = 14.sp, color = cs.onSurfaceVariant,
                                                    modifier = Modifier.padding(top = 6.dp),
                                                )
                                                OutlinedTextField(
                                                    value = vm.acceptanceNotes[l.id].orEmpty(),
                                                    onValueChange = { vm.acceptanceNotes[l.id] = it.take(500) },
                                                    placeholder = { Text("验收说明（退回时建议填写）", fontSize = 10.sp) },
                                                    minLines = 1, maxLines = 2,
                                                    shape = RoundedCornerShape(10.dp),
                                                    modifier = Modifier.fillMaxWidth().padding(top = 5.dp),
                                                )
                                                Row(
                                                    Modifier.fillMaxWidth().padding(top = 4.dp),
                                                    horizontalArrangement = Arrangement.End,
                                                ) {
                                                    TextButton(onClick = { vm.reviewDelivery(l.id, false) }) {
                                                        Text("退回复核", fontSize = 10.5.sp, color = cs.error)
                                                    }
                                                    TextButton(onClick = { vm.reviewDelivery(l.id, true) }) {
                                                        Text("接受交付", fontSize = 10.5.sp, color = Color(0xFF15803D))
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                if (l.graphNodes > 0) {
                                    Spacer(Modifier.height(4.dp))
                                    Surface(
                                        shape = RoundedCornerShape(9.dp),
                                        color = cs.surfaceVariant.copy(alpha = 0.55f),
                                        border = androidx.compose.foundation.BorderStroke(
                                            1.dp, cs.outlineVariant.copy(alpha = 0.6f)),
                                    ) {
                                        Column(Modifier.fillMaxWidth().padding(horizontal = 9.dp, vertical = 7.dp)) {
                                            Row(verticalAlignment = Alignment.CenterVertically) {
                                                Icon(Icons.Outlined.AccountTree, null, Modifier.size(12.dp),
                                                    tint = if (l.graphBlockers > 0) cs.error else cs.primary)
                                                Spacer(Modifier.width(5.dp))
                                                Text("任务证据图", fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                                                Spacer(Modifier.weight(1f))
                                                Text("${l.graphNodes} 节点 · ${l.graphEdges} 连接 · ${l.graphBlockers} 阻塞",
                                                    fontSize = 9.5.sp,
                                                    color = if (l.graphBlockers > 0) cs.error else cs.onSurfaceVariant)
                                            }
                                            if (l.graphBlockers > 0 && l.graphNextAction.isNotBlank()) {
                                                Text("下一轮优先：${l.graphNextAction}", fontSize = 9.5.sp,
                                                    lineHeight = 14.sp, color = cs.onSurfaceVariant,
                                                    modifier = Modifier.padding(top = 3.dp), maxLines = 2)
                                            }
                                            if (l.frontierUnresolved > 0) {
                                                Spacer(Modifier.height(6.dp))
                                                HorizontalDivider(color = cs.outlineVariant.copy(alpha = 0.5f))
                                                Spacer(Modifier.height(5.dp))
                                                Row(verticalAlignment = Alignment.CenterVertically) {
                                                    Text("下一工作集", fontSize = 9.5.sp, fontWeight = FontWeight.SemiBold)
                                                    Spacer(Modifier.weight(1f))
                                                    Text("${l.frontierReady} 可行 · ${l.frontierScopeBlocked} 权限受限",
                                                        fontSize = 9.sp,
                                                        color = if (l.frontierScopeBlocked > 0) cs.error else cs.onSurfaceVariant)
                                                }
                                                if (l.frontierNextAction.isNotBlank()) {
                                                    Text(l.frontierNextAction, fontSize = 9.5.sp, lineHeight = 14.sp,
                                                        color = cs.onSurfaceVariant,
                                                        modifier = Modifier.padding(top = 3.dp), maxLines = 2)
                                                }
                                            }
                                        }
                                    }
                                }
                                if (l.acceptance.isNotBlank())
                                    Text("完成标准：${l.acceptance}", fontSize = 10.5.sp, color = cs.onSurfaceVariant, maxLines = 2)
                                if (l.lastNote.isNotBlank())
                                    Text(l.lastNote, fontSize = 11.sp, color = cs.onSurfaceVariant, maxLines = 2)
                            }
                        }
                    }
                }
            }
            // 事件自动化
            if (vm.rules.isNotEmpty()) {
                item {
                    StudioCard {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.Bolt, null, tint = cs.primary, modifier = Modifier.size(15.dp))
                            Spacer(Modifier.width(6.dp))
                        Text("自动处理", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Spacer(Modifier.weight(1f))
                        Text("条件满足后自动开始", fontSize = 10.sp, color = cs.onSurfaceVariant)
                        }
                        Spacer(Modifier.height(8.dp))
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            vm.rules.forEach { r ->
                                FilterChip(selected = r.on, onClick = { vm.toggleRule(r.id, !r.on) },
                                    label = { Text(r.name, fontSize = 10.5.sp) })
                            }
                        }
                    }
                }
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }
}

private fun humanModuleState(state: String): String = when (state.lowercase()) {
    "running", "active", "working" -> "处理中"
    "waiting", "queued", "pending" -> "等待中"
    "done", "complete", "completed", "ok" -> "已完成"
    "failed", "error", "blocked" -> "需要处理"
    else -> "已连接"
}

@Composable
private fun PulseChip(label: String, value: String, ok: Boolean) {
    val cs = MaterialTheme.colorScheme
    Surface(shape = RoundedCornerShape(999.dp), color = cs.surface) {
        Row(Modifier.padding(horizontal = 10.dp, vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).background(
                if (ok) androidx.compose.ui.graphics.Color(0xFF2E7D32) else cs.error, CircleShape))
            Spacer(Modifier.width(5.dp))
            Text("$label · $value", fontSize = 10.5.sp, color = cs.onSurfaceVariant)
        }
    }
}
