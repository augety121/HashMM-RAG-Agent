package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.automirrored.outlined.FactCheck
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.outlined.AdminPanelSettings
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.BarChart
import androidx.compose.material.icons.outlined.CenterFocusStrong
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.MonitorHeart
import androidx.compose.material.icons.outlined.NetworkCheck
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.SwapHoriz
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.remote.MobileWorkRun
import com.hashmm.app.data.remote.RuntimeCapability
import com.hashmm.app.ui.components.HashMascot

private val activeWorkStates = setOf("queued", "running", "waiting_input", "waiting_approval", "blocked")

internal enum class WorkFilter(val title: String) {
    ALL("全部"),
    ATTENTION("待处理"),
    ACTIVE("进行中"),
    DONE("已完成"),
}

internal fun workMatchesFilter(run: MobileWorkRun, filter: WorkFilter): Boolean = when (filter) {
    WorkFilter.ALL -> true
    WorkFilter.ATTENTION -> run.presentation.needsUser ||
        run.status in setOf("waiting_input", "waiting_approval", "blocked", "failed", "interrupted")
    WorkFilter.ACTIVE -> run.status in setOf("queued", "running")
    WorkFilter.DONE -> run.status in setOf("completed", "delivered", "observed")
}

/**
 * 手机端“工作”是一份可继续的工作账本：
 * - Chat 是创建入口；
 * - 手机负责查看、批准、补充信息和设备接力；
 * - 桌面端负责需要浏览器、文件或电脑控制的执行；
 * - 管理/诊断收进二级入口，不再占据普通用户主流程。
 */
@Composable
fun WorkbenchHubScreen(
    isAdmin: Boolean = false,
    onOpenKnowledge: () -> Unit,
    onOpenMemory: () -> Unit,
    onNewChat: (String) -> Unit = {},
    onOpenAgentsStudio: () -> Unit = {},
    onOpenDocStudio: () -> Unit = {},
    onOpenControlHub: () -> Unit = {},
    onOpenScheduled: () -> Unit = {},
    onOpenRuns: () -> Unit = {},
    onOpenWork: (String) -> Unit = {},
    onOpenAudit: () -> Unit = {},
    onOpenQuality: () -> Unit = {},
    onOpenSelfTest: () -> Unit = {},
    onOpenContext: () -> Unit = {},
    onOpenAdvanced: () -> Unit = {},
    onOpenModels: () -> Unit = {},
    onOpenKg: () -> Unit = {},
    onOpenAdmin: () -> Unit = {},
    onOpenRelay: () -> Unit = {},
    onOpenUsage: () -> Unit = {},
    onOpenValidity: () -> Unit = {},
    viewModel: WorkbenchHubViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var filter by rememberSaveable { mutableStateOf(WorkFilter.ALL) }
    var showTools by rememberSaveable { mutableStateOf(false) }
    val runs = ui.workRuns
        .filter { workMatchesFilter(it, filter) }
        .sortedWith(
            compareByDescending<MobileWorkRun> { it.presentation.needsUser }
                .thenByDescending { it.status in activeWorkStates }
                .thenByDescending { it.updatedAt },
        )
    val resume = ui.workRuns
        .filter { it.status in activeWorkStates }
        .maxByOrNull { it.updatedAt }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 18.dp, vertical = 18.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        item {
            WorkHeader(
                backendOnline = ui.backendOnline,
                desktopOnline = ui.desktopOnline,
                desktopCount = ui.desktopCount,
                cloudWorkspaceReady = ui.cloudWorkspaceReady,
                checking = ui.checking,
                onRefresh = { viewModel.refresh(force = true) },
            )
        }
        item {
            ResumeCard(
                run = resume,
                activeCount = ui.workRuns.count { it.status in activeWorkStates },
                onOpenWork = onOpenWork,
                onNewChat = onNewChat,
            )
        }
        item {
            CreateWorkGroup(
                onNewChat = onNewChat,
                onOpenAgentsStudio = onOpenAgentsStudio,
                onOpenDocStudio = onOpenDocStudio,
                onOpenScheduled = onOpenScheduled,
            )
        }
        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                WorkFilter.entries.forEach { item ->
                    FilterChip(
                        selected = filter == item,
                        onClick = { filter = item },
                        label = { Text(item.title, fontSize = 12.sp) },
                    )
                }
            }
        }
        if (ui.checking && ui.workRuns.isEmpty()) {
            item { LoadingWork() }
        } else if (runs.isEmpty()) {
            item {
                EmptyWork(
                    filter = filter,
                    error = ui.workError,
                    onNewChat = onNewChat,
                    onOpenRuns = onOpenRuns,
                )
            }
        } else {
            items(runs, key = { it.id }) { run ->
                WorkLedgerRow(run = run, onClick = { onOpenWork(run.id) })
            }
            if (runs.size >= 10) {
                item { MoreAction("查看完整工作记录", onOpenRuns) }
            }
        }
        item {
            SectionTitle("常用位置", "需要时再进入")
            Spacer(Modifier.height(9.dp))
            GroupCard {
                ToolRow(Icons.Outlined.SwapHoriz, "设备接力", "把工作交给在线电脑继续", onOpenRelay)
                Hairline()
                ToolRow(Icons.AutoMirrored.Outlined.MenuBook, "资料", "查看这项工作可使用的文档与知识", onOpenKnowledge)
                Hairline()
                ToolRow(Icons.Outlined.Hub, "项目", "按目标、成果和验收标准组织工作", onOpenControlHub)
                Hairline()
                ToolRow(Icons.Outlined.History, "全部记录", "查看运行轨迹与中断原因", onOpenRuns)
            }
        }
        item {
            SectionTitle(if (isAdmin) "设置与管理" else "数据与信任", "默认收起")
            Spacer(Modifier.height(9.dp))
            GroupCard {
                ToolRow(
                    Icons.Outlined.Tune,
                    if (showTools) "收起" else "展开更多",
                    if (isAdmin) "连接、质量、安全和团队设置" else "授权、记忆和资料状态",
                ) { showTools = !showTools }
                if (showTools) {
                    Hairline()
                    ToolRow(Icons.AutoMirrored.Outlined.FactCheck, "授权与操作记录", "重要操作、确认和结果依据", onOpenAudit)
                    Hairline()
                    ToolRow(Icons.Outlined.Memory, "偏好与记忆", "管理跨对话保留的信息", onOpenMemory)
                    Hairline()
                    ToolRow(Icons.Outlined.Archive, "过期资料", "更新或归档失效内容", onOpenValidity)
                    Hairline()
                    ToolRow(Icons.Outlined.CenterFocusStrong, "使用中的内容", "查看当前工作实际使用的上下文", onOpenContext)
                    Hairline()
                    ToolRow(Icons.Outlined.Tune, "我的 API 与模型", "添加仅当前账号可用的供应商和模型", onOpenModels)
                    if (isAdmin) {
                        Hairline()
                        ToolRow(Icons.Outlined.MonitorHeart, "回答质量", "查看有依据的质量评测", onOpenQuality)
                        Hairline()
                        ToolRow(Icons.Outlined.NetworkCheck, "连接诊断", "检查手机、电脑与服务状态", onOpenSelfTest)
                        Hairline()
                        ToolRow(Icons.Outlined.Share, "知识关系", "维护资料实体与关系", onOpenKg)
                        Hairline()
                        ToolRow(Icons.Outlined.AdminPanelSettings, "团队与权限", "成员、角色与组织级配置", onOpenAdmin)
                        Hairline()
                        ToolRow(Icons.Outlined.BarChart, "组织用量", "处理量和花费", onOpenUsage)
                        Hairline()
                        ToolRow(Icons.Outlined.AutoAwesome, "高级治理", "隔离、派活和自动化策略", onOpenAdvanced)
                    }
                }
            }
        }
        item { Spacer(Modifier.height(4.dp)) }
    }
}

@Composable
private fun WorkHeader(
    backendOnline: Boolean,
    desktopOnline: Boolean,
    desktopCount: Int,
    cloudWorkspaceReady: Boolean,
    checking: Boolean,
    onRefresh: () -> Unit,
) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        HashMascot(Modifier.size(48.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text("工作", fontSize = 28.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.6).sp)
            Text("同一项工作，在手机查看，在电脑继续", fontSize = 12.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        ConnectionPill(backendOnline, desktopOnline, desktopCount, cloudWorkspaceReady, checking, onRefresh)
    }
}

@Composable
private fun ConnectionPill(
    backendOnline: Boolean,
    desktopOnline: Boolean,
    desktopCount: Int,
    cloudWorkspaceReady: Boolean,
    checking: Boolean,
    onClick: () -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    Surface(shape = CircleShape, color = cs.surface, modifier = Modifier.clickable(onClick = onClick)) {
        Row(Modifier.padding(horizontal = 11.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
            if (checking) {
                CircularProgressIndicator(Modifier.size(10.dp), strokeWidth = 1.6.dp)
            } else {
                Box(
                    Modifier.size(7.dp).background(
                        if (desktopOnline || cloudWorkspaceReady) Color(0xFF34C759) else if (backendOnline) Color(0xFFB7791F) else cs.error,
                        CircleShape,
                    ),
                )
            }
            Spacer(Modifier.width(6.dp))
            Text(
                workConnectionLabel(checking, desktopOnline, desktopCount, cloudWorkspaceReady, backendOnline),
                fontSize = 11.5.sp,
                fontWeight = FontWeight.SemiBold,
                color = cs.onSurfaceVariant,
            )
        }
    }
}

internal fun workConnectionLabel(
    checking: Boolean,
    desktopOnline: Boolean,
    desktopCount: Int,
    cloudWorkspaceReady: Boolean,
    backendOnline: Boolean,
): String = when {
    checking -> "同步中"
    desktopOnline && desktopCount > 1 -> "$desktopCount 台在线"
    desktopOnline -> "电脑在线"
    cloudWorkspaceReady -> "云环境可用"
    backendOnline -> "等待电脑"
    else -> "离线"
}

@Composable
private fun ResumeCard(
    run: MobileWorkRun?,
    activeCount: Int,
    onOpenWork: (String) -> Unit,
    onNewChat: (String) -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    Surface(shape = RoundedCornerShape(24.dp), color = cs.surface, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(19.dp)) {
            Text(if (run == null) "开始一项工作" else "继续最近工作", fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(5.dp))
            if (run == null) {
                Text("从目标开始，而不是先选择工具。HashMM 会把进度、资料和成果留在同一处。", fontSize = 12.5.sp, lineHeight = 18.sp, color = cs.onSurfaceVariant)
            } else {
                Text(
                    run.presentation.title.ifBlank { run.title.ifBlank { "未命名工作" } },
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    run.presentation.nextAction.ifBlank {
                        run.presentation.currentStep.ifBlank { "查看最新进度并继续" }
                    },
                    fontSize = 12.5.sp,
                    lineHeight = 18.sp,
                    color = cs.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (activeCount > 1) {
                    Spacer(Modifier.height(6.dp))
                    Text("另有 ${activeCount - 1} 项工作正在推进", fontSize = 11.sp, color = cs.primary)
                }
            }
            Spacer(Modifier.height(15.dp))
            Button(onClick = { if (run == null) onNewChat("") else onOpenWork(run.id) }) {
                Text(if (run == null) "新建工作" else "继续工作")
                Spacer(Modifier.width(6.dp))
                Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(17.dp))
            }
        }
    }
}

@Composable
private fun CreateWorkGroup(
    onNewChat: (String) -> Unit,
    onOpenAgentsStudio: () -> Unit,
    onOpenDocStudio: () -> Unit,
    onOpenScheduled: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        SectionTitle("创建", "先说目标，能力按需加入")
        GroupCard {
            ToolRow(Icons.Outlined.Psychology, "通用工作", "从 Chat 说明目标、交付物和完成标准") { onNewChat("") }
            Hairline()
            ToolRow(Icons.Outlined.Groups, "多人协作", "需要并行角色时再组建协作", onOpenAgentsStudio)
            Hairline()
            ToolRow(Icons.Outlined.Description, "资料与文档", "围绕选定资料阅读、比较并交付文件", onOpenDocStudio)
            Hairline()
            ToolRow(Icons.Outlined.Schedule, "自动任务", "按时间继续工作并回写结果", onOpenScheduled)
        }
    }
}

@Composable
private fun WorkLedgerRow(run: MobileWorkRun, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val attention = workMatchesFilter(run, WorkFilter.ATTENTION)
    Surface(
        shape = RoundedCornerShape(19.dp),
        color = cs.surface,
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
    ) {
        Row(Modifier.padding(horizontal = 15.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(kindIcon(run.kind), null, tint = if (attention) cs.primary else cs.onSurface, modifier = Modifier.size(24.dp))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    run.presentation.title.ifBlank { run.title.ifBlank { kindLabel(run.kind) } },
                    fontSize = 14.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(3.dp))
                Text(
                    run.presentation.nextAction.ifBlank {
                        run.presentation.currentStep.ifBlank { "已同步最新进度" }
                    },
                    fontSize = 11.5.sp,
                    lineHeight = 16.sp,
                    color = cs.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        run.presentation.statusLabel.ifBlank { mobileWorkStatus(run.status) },
                        fontSize = 10.5.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = if (attention) cs.primary else cs.onSurfaceVariant,
                    )
                    if (run.presentation.evidence.count > 0) {
                        Text(" · ${run.presentation.evidence.count} 条依据", fontSize = 10.5.sp, color = cs.onSurfaceVariant)
                    }
                    if (run.presentation.deliverables.isNotEmpty()) {
                        Text(" · ${run.presentation.deliverables.size} 个成果", fontSize = 10.5.sp, color = cs.onSurfaceVariant)
                    }
                }
            }
            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = cs.onSurfaceVariant, modifier = Modifier.size(18.dp))
        }
    }
}

@Composable
private fun LoadingWork() {
    Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surface) {
        Row(Modifier.fillMaxWidth().padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(12.dp))
            Text("正在同步工作记录…", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun EmptyWork(filter: WorkFilter, error: String?, onNewChat: (String) -> Unit, onOpenRuns: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Surface(shape = RoundedCornerShape(20.dp), color = cs.surface) {
        Column(Modifier.fillMaxWidth().padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                if (error == null) Icons.Outlined.CheckCircle else Icons.Outlined.Refresh,
                null,
                tint = cs.onSurfaceVariant,
                modifier = Modifier.size(30.dp),
            )
            Spacer(Modifier.height(10.dp))
            Text(
                when {
                    error != null -> "工作记录暂时无法更新"
                    filter == WorkFilter.ATTENTION -> "没有等待你处理的工作"
                    filter == WorkFilter.ACTIVE -> "当前没有进行中的工作"
                    filter == WorkFilter.DONE -> "还没有已完成的工作"
                    else -> "还没有工作记录"
                },
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(5.dp))
            Text(
                if (error != null) "不会用示例内容代替真实状态，恢复连接后再试。"
                else "从 Chat 说明你想完成什么，手机和桌面会显示同一进度。",
                fontSize = 12.sp,
                color = cs.onSurfaceVariant,
            )
            Spacer(Modifier.height(13.dp))
            Button(onClick = { if (error != null) onOpenRuns() else onNewChat("") }) {
                Text(if (error != null) "查看最近记录" else "开始工作")
            }
        }
    }
}

@Composable
private fun SectionTitle(title: String, hint: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(title, Modifier.weight(1f), fontSize = 15.sp, fontWeight = FontWeight.Bold)
        Text(hint, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun GroupCard(content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth()) {
        Column(content = content)
    }
}

@Composable
private fun ToolRow(icon: ImageVector, title: String, subtitle: String, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, modifier = Modifier.size(21.dp))
        Spacer(Modifier.width(13.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.5.sp, fontWeight = FontWeight.Medium)
            Text(subtitle, fontSize = 11.5.sp, color = cs.onSurfaceVariant)
        }
        Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = cs.onSurfaceVariant, modifier = Modifier.size(18.dp))
    }
}

@Composable
private fun MoreAction(label: String, onClick: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.surface,
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(label, Modifier.weight(1f), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, modifier = Modifier.size(18.dp))
        }
    }
}

@Composable
private fun Hairline() {
    Box(
        Modifier.fillMaxWidth().padding(start = 50.dp).height(0.5.dp)
            .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.65f)),
    )
}

private fun kindLabel(kind: String): String = when (kind) {
    "chat" -> "对话工作"
    "loop" -> "持续任务"
    "team" -> "协作任务"
    "browser" -> "浏览器调研"
    "computer" -> "电脑任务"
    "remote" -> "设备接力"
    "artifact" -> "文档成果"
    else -> "工作"
}

private fun kindIcon(kind: String): ImageVector = when (kind) {
    "team" -> Icons.Outlined.Groups
    "loop" -> Icons.Outlined.Schedule
    "artifact" -> Icons.Outlined.Description
    "browser" -> Icons.Outlined.Language
    "computer", "remote" -> Icons.Outlined.Computer
    else -> Icons.Outlined.Hub
}

internal fun mobileWorkStatus(status: String): String = when (status) {
    "queued" -> "已排队"
    "running" -> "执行中"
    "waiting_input" -> "等待输入"
    "waiting_approval" -> "等待批准"
    "blocked" -> "已阻塞"
    "observed" -> "已有记录"
    "delivered" -> "已交付"
    "completed" -> "已验证完成"
    "failed" -> "失败"
    "cancelled" -> "已取消"
    "interrupted" -> "已中断"
    else -> "状态未知"
}

internal fun capabilityStatusLabel(
    capability: RuntimeCapability,
    desktopOnline: Boolean,
    freshness: String = "live",
): String = when {
    capability.requiresDesktop && capability.productionReady && !desktopOnline -> "等待电脑"
    capability.productionReady && freshness != "live" -> "最近可用"
    capability.productionReady -> "可用"
    capability.state == "setup_required" -> "待配置"
    capability.state == "degraded" -> "部分可用"
    capability.state == "disabled" -> "已关闭"
    else -> "未接通"
}

internal fun capabilityDetail(capability: RuntimeCapability, status: String): String {
    if (status == "等待电脑") return "打开同账号桌面端后，任务会在这台电脑继续"
    if (status == "最近可用") return "上次验证时可用；连接恢复后会自动复核，不把缓存当作当前事实"
    val reason = capability.reason.trim()
    if (reason.isNotBlank() && !reason.equals("ok", ignoreCase = true)) return reason
    return when (capability.id) {
        "browser_use" -> "可从 Chat 发起网页读取、检索与受控操作"
        "computer_use" -> "可读取文件并在确认后执行电脑操作"
        "artifacts" -> "文档、表格和演示结果可跨端查看与继续处理"
        "multi_agent" -> "可按角色并行推进，并汇总为一项可复核交付"
        "canvas" -> "画布产物已接入 Chat，可编辑、留存版本并继续追问"
        "long_tasks" -> "持续任务、运行轨迹和上下文检查点已接入"
        else -> "能力已接入当前账号与 Chat"
    }
}
