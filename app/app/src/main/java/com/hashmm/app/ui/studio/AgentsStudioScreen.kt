package com.hashmm.app.ui.studio

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.AgentInfo
import com.hashmm.app.data.remote.StudioRepository
import com.hashmm.app.data.remote.TeamRole
import com.hashmm.app.data.remote.TeamStatusA
import com.hashmm.app.ui.components.ScreenHeader
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 智能体工坊（App 原生版，V259）：选人 → 分工 → 并行执行 → 汇总。与桌面端同一套引擎。 */
@HiltViewModel
class AgentsStudioViewModel @Inject constructor(private val repo: StudioRepository) : ViewModel() {
    var agents by mutableStateOf<List<AgentInfo>>(emptyList()); private set
    var sourceLoading by mutableStateOf(true); private set
    var sourceError by mutableStateOf(""); private set
    var picked = mutableStateListOf<String>()
    var autoMode by mutableStateOf(true)
    var goal by mutableStateOf("")
    var roles = mutableStateListOf<TeamRole>()
    var phase by mutableStateOf("pick")          // pick / preview / running / done
    var busy by mutableStateOf(false); private set
    var err by mutableStateOf("")
    var status by mutableStateOf<TeamStatusA?>(null); private set
    var conversationId by mutableStateOf(""); private set
    private var targetConversationId = ""
    var routeMode by mutableStateOf("auto")
    var execMode by mutableStateOf("parallel")   // parallel / pipeline

    init { loadSources() }

    fun loadSources() {
        sourceLoading = true
        sourceError = ""
        viewModelScope.launch {
            val result = repo.agentsResult()
            agents = result.items
            sourceError = result.error.ifBlank {
                if (result.items.isEmpty()) "成员库当前为空，请先在桌面端配置可用智能体" else ""
            }
            routeMode = repo.routeMode()
            sourceLoading = false
        }
    }

    fun togglePick(id: String) {
        if (picked.contains(id)) picked.remove(id) else if (picked.size < 4) picked.add(id)
    }

    fun setRoute(mode: String) {
        val previous = routeMode
        routeMode = mode
        viewModelScope.launch {
            if (!repo.setRouteMode(mode)) {
                routeMode = previous
                err = "编队策略保存失败，请检查连接后重试"
            }
        }
    }

    fun bindConversation(convId: String, initialGoal: String) {
        if (phase != "pick") return
        if (convId.isNotBlank()) targetConversationId = convId
        if (goal.isBlank() && initialGoal.isNotBlank()) goal = initialGoal
    }

    fun preview() {
        val g = goal.trim()
        if (g.isBlank()) { err = "先写一句团队目标"; return }
        if (!autoMode && picked.size < 2) { err = "手动编队至少勾选 2 个成员（最多 4）"; return }
        err = ""; busy = true
        viewModelScope.launch {
            val (rs, e) = repo.preview(g, if (autoMode) null else picked.toList())
            busy = false
            if (rs.isEmpty()) { err = e.ifBlank { "编队失败" }; return@launch }
            roles.clear(); roles.addAll(rs); phase = "preview"
        }
    }

    fun start() {
        val g = goal.trim()
        val rs = roles.filter { it.role.isNotBlank() && it.task.isNotBlank() }
        if (rs.size < 2) { err = "至少保留 2 个成员"; return }
        err = ""; busy = true
        viewModelScope.launch {
            val cid = targetConversationId.ifBlank { "c" + System.currentTimeMillis().toString(36) }
            if (targetConversationId.isBlank() && !repo.createConversation(cid, g.take(20))) {
                busy = false
                err = "创建结果会话失败，请检查登录状态和后端连接"
                return@launch
            }
            conversationId = cid
            val (tid, e) = repo.startTeam(g, cid, rs, execMode)
            busy = false
            if (tid.isBlank()) { err = e.ifBlank { "启动失败" }; return@launch }
            phase = "running"
            var consecutiveMisses = 0
            while (phase == "running") {
                delay(1500)
                val st = repo.teamStatus(tid)
                if (st == null) {
                    consecutiveMisses += 1
                    if (consecutiveMisses >= 3) {
                        err = "执行仍在服务器继续，但手机暂时无法同步进度"
                        delay(3500)
                    }
                    continue
                }
                consecutiveMisses = 0
                err = ""
                status = st
                if (st.status != "running") phase = "done"
            }
        }
    }

    fun reset() { phase = "pick"; roles.clear(); status = null; conversationId = ""; err = "" }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AgentsStudioScreen(
    existingConversationId: String = "",
    initialGoal: String = "",
    onBack: () -> Unit,
    onOpenConversation: (String) -> Unit = {},
    vm: AgentsStudioViewModel = hiltViewModel(),
) {
    val cs = MaterialTheme.colorScheme
    var showPreferences by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(existingConversationId, initialGoal) {
        vm.bindConversation(existingConversationId, initialGoal)
    }
    Scaffold(topBar = {
        ScreenHeader(
            title = "协作任务",
            subtitle = if (existingConversationId.isNotBlank()) "围绕当前对话分工，完成后汇总回来" else "把复杂目标拆给多个助手共同完成",
            onBack = onBack,
            actions = {
                if (vm.phase != "pick") TextButton(onClick = { vm.reset() }) { Text("重新编队") }
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
                    steps = listOf("目标", "分工", "执行", "交付"),
                    activeIndex = when (vm.phase) {
                        "preview" -> 1
                        "running" -> 2
                        "done" -> 3
                        else -> 0
                    },
                )
            }
            // 目标 + 模式
            item {
                StudioCard {
                    Text("这次要完成什么", fontWeight = FontWeight.Bold, fontSize = 15.sp)
                    Text("写清目标和最终交付形式，系统会先给出分工供你确认。", fontSize = 11.5.sp, color = cs.onSurfaceVariant)
                    Spacer(Modifier.height(9.dp))
                    OutlinedTextField(
                        value = vm.goal, onValueChange = { vm.goal = it },
                        enabled = vm.phase == "pick" || vm.phase == "preview",
                        label = { Text("目标与交付要求") },
                        placeholder = { Text("例：调研三款竞品，给出带依据的选型报告") },
                        minLines = 2, shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(10.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        SingleChoiceSegmentedButtonRow {
                            SegmentedButton(selected = vm.autoMode, onClick = { vm.autoMode = true },
                                shape = SegmentedButtonDefaults.itemShape(0, 2), enabled = vm.phase == "pick") { Text("智能编队") }
                            SegmentedButton(selected = !vm.autoMode, onClick = { vm.autoMode = false },
                                shape = SegmentedButtonDefaults.itemShape(1, 2), enabled = vm.phase == "pick") { Text("手动选择") }
                        }
                        Spacer(Modifier.weight(1f))
                        when (vm.phase) {
                            "pick" -> Button(onClick = { vm.preview() }, enabled = !vm.busy, shape = RoundedCornerShape(12.dp)) {
                                if (vm.busy) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = cs.onPrimary)
                                else Text("预览分工")
                            }
                            "preview" -> Button(onClick = { vm.start() }, enabled = !vm.busy, shape = RoundedCornerShape(12.dp)) {
                                if (vm.busy) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = cs.onPrimary)
                                else Text("启动执行")
                            }
                        }
                    }
                    if (vm.err.isNotBlank()) {
                        Spacer(Modifier.height(6.dp))
                        Text(vm.err, color = cs.error, fontSize = 12.sp)
                    }
                }
            }
            // 成员库（选人阶段）
            if (vm.phase == "pick") {
                item { Text("选择参与者", fontWeight = FontWeight.Bold, fontSize = 14.sp, modifier = Modifier.padding(start = 4.dp)) }
                if (vm.sourceLoading) item {
                    com.hashmm.app.ui.components.HmmStateView(
                        kind = com.hashmm.app.ui.components.HmmStateKind.Loading,
                        message = "正在读取可用成员",
                    )
                }
                if (!vm.sourceLoading && vm.agents.isEmpty()) item {
                    com.hashmm.app.ui.components.HmmStateView(
                        kind = com.hashmm.app.ui.components.HmmStateKind.Error,
                        title = "暂时无法编队",
                        message = vm.sourceError,
                        icon = Icons.Outlined.Groups,
                        onRetry = vm::loadSources,
                    )
                }
                items(vm.agents.chunked(2)) { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        row.forEach { a ->
                            val on = vm.picked.contains(a.id)
                            Surface(
                                shape = RoundedCornerShape(16.dp),
                                color = if (on) cs.primaryContainer else cs.surfaceVariant.copy(alpha = 0.55f),
                                border = if (on) androidx.compose.foundation.BorderStroke(1.5.dp, cs.primary) else null,
                                modifier = Modifier.weight(1f)
                                    .clickable(enabled = !vm.autoMode) { vm.togglePick(a.id) },
                            ) {
                                Column(Modifier.padding(12.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        InitialBadge(a.name, on)
                                        Spacer(Modifier.width(8.dp))
                                        Text(a.name, fontWeight = FontWeight.SemiBold, fontSize = 13.5.sp)
                                        Spacer(Modifier.weight(1f))
                                        if (on) Icon(Icons.Outlined.CheckCircle, null, tint = cs.primary, modifier = Modifier.size(16.dp))
                                    }
                                    Spacer(Modifier.height(5.dp))
                                    Text(a.skill, fontSize = 11.sp, color = cs.onSurfaceVariant, lineHeight = 15.sp)
                                }
                            }
                        }
                        if (row.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
                item {
                    Text(if (vm.autoMode) "智能编队：系统从成员库自动挑 2-4 个最合适的"
                         else "已选 ${vm.picked.size}/4 · 点卡片勾选",
                        fontSize = 11.5.sp, color = cs.onSurfaceVariant, modifier = Modifier.padding(start = 4.dp))
                }
            }
            // 分工预览 / 执行泳道
            val lane = vm.status?.roles ?: vm.roles
            if (vm.phase != "pick" && lane.isNotEmpty()) {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(if (vm.phase == "preview") "确认分工" else "团队进度",
                            fontWeight = FontWeight.Bold, fontSize = 14.sp, modifier = Modifier.padding(start = 4.dp))
                        Spacer(Modifier.weight(1f))
                        if (vm.phase == "preview") {
                            FilterChip(selected = vm.execMode == "parallel", onClick = { vm.execMode = "parallel" },
                                label = { Text("并行", fontSize = 10.5.sp) }, modifier = Modifier.height(28.dp))
                            Spacer(Modifier.width(4.dp))
                            FilterChip(selected = vm.execMode == "pipeline", onClick = { vm.execMode = "pipeline" },
                                label = { Text("流水线", fontSize = 10.5.sp) }, modifier = Modifier.height(28.dp))
                        } else {
                            val done = lane.count { it.state == "ok" }
                            Text("$done/${lane.size} 完成", fontSize = 11.5.sp, color = cs.onSurfaceVariant)
                        }
                    }
                }
                items(lane.size) { i ->
                    val r = lane[i]
                    StudioCard {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            InitialBadge(r.role, r.state == "run")
                            Spacer(Modifier.width(8.dp))
                            Text(r.role, fontWeight = FontWeight.SemiBold, fontSize = 13.5.sp)
                            Spacer(Modifier.weight(1f))
                            if (vm.phase != "preview") StateDot(r.state)
                        }
                        Spacer(Modifier.height(6.dp))
                        if (vm.phase == "preview") {
                            OutlinedTextField(
                                value = vm.roles[i].task,
                                onValueChange = { vm.roles[i] = vm.roles[i].copy(task = it.take(120)) },
                                textStyle = androidx.compose.ui.text.TextStyle(fontSize = 12.5.sp),
                                shape = RoundedCornerShape(12.dp),
                                modifier = Modifier.fillMaxWidth(),
                            )
                        } else {
                            Text(r.task, fontSize = 12.sp, color = cs.onSurfaceVariant)
                            if (r.finding.isNotBlank()) {
                                Spacer(Modifier.height(6.dp)); HorizontalDivider()
                                Spacer(Modifier.height(6.dp))
                                Text(r.finding, fontSize = 12.sp, lineHeight = 17.sp)
                            }
                            if (r.err.isNotBlank()) Text("失败：${r.err}", fontSize = 11.sp, color = cs.error)
                        }
                    }
                }
            }
            vm.status?.takeIf { it.gateStatus.isNotBlank() || it.graphNodes > 0 || it.frontierUnresolved > 0 }?.let { st ->
                item {
                    Surface(
                        shape = RoundedCornerShape(16.dp),
                        color = cs.surfaceVariant.copy(alpha = 0.48f),
                        border = androidx.compose.foundation.BorderStroke(1.dp, cs.outlineVariant),
                    ) {
                        Column(Modifier.fillMaxWidth().padding(12.dp)) {
                            if (st.gateStatus.isNotBlank()) {
                                val gateOk = st.gateStatus == "verified"
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(if (gateOk) Icons.Outlined.CheckCircle else Icons.Outlined.PendingActions,
                                        null, Modifier.size(14.dp), tint = if (gateOk) Color(0xFF15803D) else Color(0xFF9A6700))
                                    Spacer(Modifier.width(6.dp))
                                    Text("协作完成门", fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
                                    Spacer(Modifier.weight(1f))
                                    Text(if (gateOk) "已验证" else if (st.gateStatus == "delivered_with_limits") "待复核" else "未闭环",
                                        fontSize = 10.sp, color = if (gateOk) Color(0xFF15803D) else Color(0xFF9A6700))
                                }
                                Text("${st.gatePassed}/${st.gateRequired} 必需条件通过 · 失败 ${st.gateFailed} · 待核验 ${st.gateReview}",
                                    fontSize = 10.5.sp, color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
                                if (st.gateNextAction.isNotBlank()) Text("下一步：${st.gateNextAction}", fontSize = 10.sp,
                                    lineHeight = 14.sp, color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 3.dp))
                                if (st.graphNodes > 0) { Spacer(Modifier.height(8.dp)); HorizontalDivider(); Spacer(Modifier.height(7.dp)) }
                            }
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Outlined.AccountTree, null, Modifier.size(14.dp),
                                    tint = if (st.graphBlockers > 0) cs.error else cs.primary)
                                Spacer(Modifier.width(6.dp))
                                Text("协作证据图", fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
                                Spacer(Modifier.weight(1f))
                                Text("${st.graphNodes} 节点 · ${st.graphEdges} 连接 · ${st.graphBlockers} 阻塞",
                                    fontSize = 10.sp,
                                    color = if (st.graphBlockers > 0) cs.error else cs.onSurfaceVariant)
                            }
                            if (st.graphBlockers > 0 && st.graphNextAction.isNotBlank()) {
                                Text("建议：${st.graphNextAction}", fontSize = 10.5.sp, lineHeight = 15.sp,
                                    color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 5.dp))
                            } else {
                                Text("分工、共享来源、角色状态和交付检查来自同一后端任务。",
                                    fontSize = 10.5.sp, color = cs.onSurfaceVariant,
                                    modifier = Modifier.padding(top = 5.dp))
                            }
                            if (st.frontierUnresolved > 0) {
                                Spacer(Modifier.height(8.dp))
                                HorizontalDivider(color = cs.outlineVariant.copy(alpha = 0.55f))
                                Spacer(Modifier.height(7.dp))
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text("下一工作集", fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold)
                                    Spacer(Modifier.weight(1f))
                                    Text("${st.frontierReady} 可行 · ${st.frontierScopeBlocked} 权限受限",
                                        fontSize = 9.5.sp,
                                        color = if (st.frontierScopeBlocked > 0) cs.error else cs.onSurfaceVariant)
                                }
                                if (st.frontierNextAction.isNotBlank()) {
                                    Text(st.frontierNextAction, fontSize = 10.5.sp, lineHeight = 15.sp,
                                        color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
                                }
                                Text("可行路线不代表已批准或已执行。", fontSize = 9.5.sp,
                                    color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 3.dp))
                            }
                        }
                    }
                }
            }
            // 汇总
            vm.status?.final?.takeIf { it.isNotBlank() }?.let { fin ->
                item {
                    Surface(shape = RoundedCornerShape(18.dp), color = cs.primaryContainer.copy(alpha = 0.55f)) {
                        Column(Modifier.padding(14.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Outlined.AutoAwesome, null, tint = cs.primary, modifier = Modifier.size(15.dp))
                                Spacer(Modifier.width(6.dp))
                                Text("团队汇总（已回帖会话）", fontWeight = FontWeight.Bold, fontSize = 13.sp, color = cs.primary)
                            }
                            Spacer(Modifier.height(6.dp))
                            Text(fin, fontSize = 13.sp, lineHeight = 19.sp)
                            if (vm.conversationId.isNotBlank()) {
                                Spacer(Modifier.height(10.dp))
                                Button(
                                    onClick = { onOpenConversation(vm.conversationId) },
                                    modifier = Modifier.fillMaxWidth(),
                                    shape = RoundedCornerShape(12.dp),
                                ) { Text("在对话中继续") }
                            }
                        }
                    }
                }
            }
            item {
                TextButton(
                    onClick = { showPreferences = !showPreferences },
                    contentPadding = PaddingValues(horizontal = 4.dp),
                ) {
                    Icon(if (showPreferences) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                        contentDescription = null, modifier = Modifier.size(17.dp))
                    Spacer(Modifier.width(5.dp))
                    Text(if (showPreferences) "收起协作偏好" else "协作偏好")
                }
                if (showPreferences) {
                    StudioCard {
                        Text("普通对话如何选择助手", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Text("这是整个账号的偏好，不会改变本次已经确认的分工。", fontSize = 11.5.sp, color = cs.onSurfaceVariant)
                        Spacer(Modifier.height(8.dp))
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            FilterChip(selected = vm.routeMode == "auto", onClick = { vm.setRoute("auto") }, label = { Text("智能选择") })
                            FilterChip(selected = vm.routeMode == "off", onClick = { vm.setRoute("off") }, label = { Text("不自动调用") })
                            val fixed = vm.agents.firstOrNull { it.id == vm.routeMode }
                            if (fixed != null) FilterChip(selected = true, onClick = { vm.setRoute("auto") }, label = { Text("固定：${fixed.name}") })
                        }
                    }
                }
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }
}

/* ── 共用小件（三工坊统一视觉）── */
@Composable
internal fun StudioCard(content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().padding(16.dp), content = content)
    }
}

@Composable
internal fun StudioStageBar(steps: List<String>, activeIndex: Int) {
    val cs = MaterialTheme.colorScheme
    Surface(shape = RoundedCornerShape(18.dp), color = cs.surface) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 13.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            steps.forEachIndexed { index, step ->
                val reached = index <= activeIndex
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.size(24.dp).background(
                            if (reached) cs.primary else cs.surfaceVariant,
                            CircleShape,
                        ),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            if (index < activeIndex) "✓" else "${index + 1}",
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            color = if (reached) cs.onPrimary else cs.onSurfaceVariant,
                        )
                    }
                    Spacer(Modifier.width(5.dp))
                    Text(
                        step,
                        fontSize = 11.5.sp,
                        fontWeight = if (index == activeIndex) FontWeight.Bold else FontWeight.Medium,
                        color = if (reached) cs.onSurface else cs.onSurfaceVariant,
                    )
                }
                if (index < steps.lastIndex) {
                    Box(
                        Modifier.weight(1f).padding(horizontal = 6.dp).height(1.dp)
                            .background(if (index < activeIndex) cs.primary else cs.outlineVariant),
                    )
                }
            }
        }
    }
}

@Composable
internal fun InitialBadge(name: String, active: Boolean) {
    val cs = MaterialTheme.colorScheme
    Box(
        Modifier.size(26.dp).background(if (active) cs.primary else cs.primaryContainer, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Text(name.take(1), fontSize = 12.sp, fontWeight = FontWeight.Bold,
            color = if (active) cs.onPrimary else cs.primary)
    }
}

@Composable
internal fun StateDot(state: String) {
    val cs = MaterialTheme.colorScheme
    val (color, label) = when (state) {
        "run" -> cs.tertiary to "执行中"
        "ok" -> androidx.compose.ui.graphics.Color(0xFF2E7D32) to "完成"
        "fail" -> cs.error to "失败"
        else -> cs.outline to "等待"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(7.dp).background(color, CircleShape))
        Spacer(Modifier.width(4.dp))
        Text(label, fontSize = 11.sp, color = color, fontWeight = FontWeight.Medium)
    }
}
