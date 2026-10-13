package com.hashmm.app.ui.activity

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
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
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
import com.hashmm.app.data.remote.MobileWorkActionItem
import com.hashmm.app.data.remote.MobileWorkRun
import com.hashmm.app.data.remote.RuntimeCapability
import com.hashmm.app.data.remote.ScheduledTask
import com.hashmm.app.ui.workbench.mobileWorkStatus
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val activeStates = setOf("queued", "running")
private val attentionStates = setOf("waiting_input", "waiting_approval", "blocked", "failed", "interrupted")
private val completedStates = setOf("completed", "delivered", "observed")

internal data class QuickActionAvailability(val enabled: Boolean, val reason: String = "")

internal fun quickActionAvailability(
    capabilities: List<RuntimeCapability>,
    capabilityId: String,
    desktopOnline: Boolean,
): QuickActionAvailability {
    val capability = capabilities.firstOrNull {
        it.id == capabilityId && it.visibility == "user" && !it.diagnosticOnly
    } ?: return QuickActionAvailability(false, "当前服务端没有返回这项能力")
    if (!capability.productionReady) {
        return QuickActionAvailability(false, capability.reason.ifBlank { "这项能力尚未接通" })
    }
    if (capability.requiresDesktop && !desktopOnline) {
        return QuickActionAvailability(false, "打开同账号桌面端后可用")
    }
    return QuickActionAvailability(true)
}

internal enum class TodayBucket { ATTENTION, ACTIVE, COMPLETED, OTHER }

internal fun todayBucket(status: String, needsUser: Boolean): TodayBucket = when {
    needsUser || status in attentionStates -> TodayBucket.ATTENTION
    status in activeStates -> TodayBucket.ACTIVE
    status in completedStates -> TodayBucket.COMPLETED
    else -> TodayBucket.OTHER
}

internal fun isSameLocalDay(epochSeconds: Double, nowMillis: Long = System.currentTimeMillis()): Boolean {
    if (epochSeconds <= 0) return false
    val millis = if (epochSeconds > 1_000_000_000_000.0) epochSeconds.toLong() else (epochSeconds * 1000).toLong()
    val zone = ZoneId.systemDefault()
    return Instant.ofEpochMilli(millis).atZone(zone).toLocalDate() ==
        Instant.ofEpochMilli(nowMillis).atZone(zone).toLocalDate()
}

/**
 * 手机端“今天”是工作收件箱，而不是桌面控制台的缩小版。
 * 页面只回答四个问题：现在最该处理什么、哪些工作仍在推进、今天交付了什么、
 * 下一项自动任务是什么。所有状态来自同账号工作事实与自动任务 API。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ActivityScreen(
    onBack: () -> Unit,
    showBack: Boolean = true,
    onOpenConversation: (String) -> Unit,
    onNewChat: (String) -> Unit = {},
    onOpenRemote: () -> Unit = {},
    onOpenTaskLauncher: (String, String) -> Unit = { _, _ -> },
    onOpenRuns: () -> Unit = {},
    onOpenWork: (String) -> Unit = {},
    onOpenScheduled: () -> Unit = {},
    viewModel: ActivityViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val work = ui.work
    val inboxByRun = work.actionInbox.items.associateBy { it.runId }
    val attention = work.runs
        .filter { todayBucket(it.status, it.presentation.needsUser || inboxByRun.containsKey(it.id)) == TodayBucket.ATTENTION }
        .sortedWith(compareByDescending<MobileWorkRun> { inboxByRun[it.id]?.priority == "high" }.thenByDescending { it.updatedAt })
    val active = work.runs
        .filter { todayBucket(it.status, it.presentation.needsUser) == TodayBucket.ACTIVE }
        .sortedByDescending { it.updatedAt }
    val completed = work.runs
        .filter { todayBucket(it.status, it.presentation.needsUser) == TodayBucket.COMPLETED && isSameLocalDay(it.updatedAt) }
        .sortedByDescending { it.updatedAt }
    val nextRoutine = ui.schedules.tasks
        .filter { it.enabled && it.nextRunSec > 0 }
        .minByOrNull { it.nextRunSec }

    PullToRefreshBox(
        isRefreshing = ui.refreshing,
        onRefresh = viewModel::refreshAll,
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(horizontal = 18.dp, vertical = 18.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            item {
                TodayHeader(showBack = showBack, onBack = onBack)
            }
            if (ui.loading && work.runs.isEmpty()) {
                item { LoadingToday() }
            } else {
                item {
                    FocusCard(
                        attention = attention.firstOrNull(),
                        activeCount = active.size,
                        completedCount = completed.size,
                        onOpenWork = onOpenWork,
                        onNewChat = onNewChat,
                    )
                }
                if (!work.error.isNullOrBlank()) {
                    item {
                        InlineNotice(
                            title = "暂时无法更新",
                            detail = "正在显示这台手机上最近同步的工作；恢复连接后下拉刷新。",
                        )
                    }
                } else if (!work.notice.isNullOrBlank()) {
                    item { InlineNotice("最近同步", work.notice.orEmpty()) }
                }
                if (attention.isNotEmpty()) {
                    item { SectionHeader("需要你处理", "${attention.size} 项") }
                    items(attention, key = { "attention-${it.id}" }) { run ->
                        WorkRow(
                            run = run,
                            action = inboxByRun[run.id],
                            emphasis = true,
                            onClick = { onOpenWork(run.id) },
                        )
                    }
                }
                if (active.isNotEmpty()) {
                    item { SectionHeader("正在推进", "${active.size} 项") }
                    item {
                        WorkGroup {
                            active.take(4).forEachIndexed { index, run ->
                                WorkRow(
                                    run = run,
                                    action = null,
                                    emphasis = false,
                                    onClick = { onOpenWork(run.id) },
                                )
                                if (index < active.take(4).lastIndex) Hairline()
                            }
                            if (active.size > 4) {
                                Hairline()
                                MoreRow("查看全部 ${active.size} 项", onOpenRuns)
                            }
                        }
                    }
                }
                item {
                    TodayResultSection(
                        completed = completed,
                        onOpenWork = onOpenWork,
                        onOpenRuns = onOpenRuns,
                    )
                }
                item {
                    NextRoutineCard(
                        task = nextRoutine,
                        error = ui.schedules.error,
                        onOpenScheduled = onOpenScheduled,
                    )
                }
                item {
                    QuickStartCard(
                        onNewChat = onNewChat,
                        onOpenRemote = onOpenRemote,
                        onBrowserTask = { onOpenTaskLauncher("browser", "") },
                    )
                }
            }
            item { Spacer(Modifier.height(4.dp)) }
        }
    }
}

@Composable
private fun TodayHeader(showBack: Boolean, onBack: () -> Unit) {
    val date = LocalDate.now()
    val locale = Locale.getDefault()
    val dateText = date.format(DateTimeFormatter.ofPattern("M月d日 EEEE", locale))
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        if (showBack) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
            }
        }
        Column(Modifier.weight(1f)) {
            Text("今天", fontSize = 28.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.6).sp)
            Text(dateText, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun LoadingToday() {
    Surface(shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.surface) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 28.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(12.dp))
            Text("正在同步今天的工作…", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun FocusCard(
    attention: MobileWorkRun?,
    activeCount: Int,
    completedCount: Int,
    onOpenWork: (String) -> Unit,
    onNewChat: (String) -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    val title = when {
        attention != null -> "先处理这一项"
        activeCount > 0 -> "工作正在继续"
        completedCount > 0 -> "今天已有交付"
        else -> "今天从一件事开始"
    }
    val body = when {
        attention != null -> attention.presentation.nextAction.ifBlank {
            attention.presentation.currentStep.ifBlank { "这项工作需要你的确认或补充信息。" }
        }
        activeCount > 0 -> "$activeCount 项工作仍在推进，离开 App 也不会丢失进度。"
        completedCount > 0 -> "已完成 $completedCount 项工作，结果和依据可以随时回来查看。"
        else -> "告诉 HashMM 你想完成什么，后续进度会回到这里。"
    }
    Surface(
        shape = RoundedCornerShape(24.dp),
        color = cs.primaryContainer.copy(alpha = 0.68f),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(20.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    if (attention != null) Icons.Outlined.NotificationsActive else Icons.Outlined.TaskAlt,
                    contentDescription = null,
                    tint = cs.primary,
                    modifier = Modifier.size(28.dp),
                )
                Spacer(Modifier.width(12.dp))
                Text(title, fontSize = 19.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(14.dp))
            if (attention != null) {
                Text(
                    attention.presentation.title.ifBlank { attention.title.ifBlank { "未命名工作" } },
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(5.dp))
            }
            Text(body, fontSize = 13.sp, lineHeight = 19.sp, color = cs.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))
            Button(onClick = {
                if (attention != null) onOpenWork(attention.id) else onNewChat("")
            }) {
                Text(if (attention != null) "继续处理" else "开始工作")
                Spacer(Modifier.width(6.dp))
                Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(17.dp))
            }
        }
    }
}

@Composable
private fun InlineNotice(title: String, detail: String) {
    Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.7f)) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 12.dp)) {
            Text(title, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(2.dp))
            Text(detail, fontSize = 11.5.sp, lineHeight = 16.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SectionHeader(title: String, hint: String = "") {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(title, Modifier.weight(1f), fontSize = 15.sp, fontWeight = FontWeight.Bold)
        if (hint.isNotBlank()) Text(hint, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun WorkGroup(content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth(), content = content)
    }
}

@Composable
private fun WorkRow(
    run: MobileWorkRun,
    action: MobileWorkActionItem?,
    emphasis: Boolean,
    onClick: () -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    val title = action?.title.orEmpty().ifBlank {
        run.presentation.title.ifBlank { run.title.ifBlank { workKind(run.kind) } }
    }
    val detail = action?.summary.orEmpty().ifBlank {
        run.presentation.nextAction.ifBlank {
            run.presentation.currentStep.ifBlank { run.presentation.progress.label.ifBlank { "已同步最新进度" } }
        }
    }
    Surface(
        shape = RoundedCornerShape(20.dp),
        color = if (emphasis) cs.surface else Color.Transparent,
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
    ) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(workIcon(run.kind), null, tint = if (emphasis) cs.primary else cs.onSurface, modifier = Modifier.size(24.dp))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 14.5.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(3.dp))
                Text(detail, maxLines = 2, overflow = TextOverflow.Ellipsis, fontSize = 11.5.sp, lineHeight = 16.sp, color = cs.onSurfaceVariant)
                Spacer(Modifier.height(5.dp))
                Text(
                    run.presentation.statusLabel.ifBlank { mobileWorkStatus(run.status) } + " · " + formatAgo(run.updatedAt),
                    fontSize = 10.5.sp,
                    color = if (emphasis) cs.primary else cs.onSurfaceVariant,
                )
            }
            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = cs.onSurfaceVariant, modifier = Modifier.size(18.dp))
        }
    }
}

@Composable
private fun TodayResultSection(
    completed: List<MobileWorkRun>,
    onOpenWork: (String) -> Unit,
    onOpenRuns: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        SectionHeader("今天的成果", if (completed.isEmpty()) "暂无" else "${completed.size} 项")
        WorkGroup {
            if (completed.isEmpty()) {
                Row(Modifier.padding(horizontal = 16.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.CheckCircle, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(12.dp))
                    Text("完成的工作会在这里留下结果与依据", fontSize = 12.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                completed.take(3).forEachIndexed { index, run ->
                    WorkRow(run, null, emphasis = false) { onOpenWork(run.id) }
                    if (index < completed.take(3).lastIndex) Hairline()
                }
                if (completed.size > 3) {
                    Hairline()
                    MoreRow("查看今天全部成果", onOpenRuns)
                }
            }
        }
    }
}

@Composable
private fun NextRoutineCard(task: ScheduledTask?, error: String?, onOpenScheduled: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        SectionHeader("下一项安排")
        Surface(
            shape = RoundedCornerShape(20.dp),
            color = cs.surface,
            modifier = Modifier.fillMaxWidth().clickable(onClick = onOpenScheduled),
        ) {
            Row(Modifier.padding(horizontal = 16.dp, vertical = 15.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Schedule, null, modifier = Modifier.size(24.dp))
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(task?.name ?: "自动任务", fontSize = 14.5.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(3.dp))
                    Text(
                        when {
                            task != null -> "${formatTime(task.nextRunSec)} · ${routineAction(task.action)}"
                            !error.isNullOrBlank() -> "暂时无法同步安排，点此查看"
                            else -> "还没有安排，创建后会按时执行并回写结果"
                        },
                        fontSize = 11.5.sp,
                        color = cs.onSurfaceVariant,
                    )
                }
                Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = cs.onSurfaceVariant, modifier = Modifier.size(18.dp))
            }
        }
    }
}

@Composable
private fun QuickStartCard(
    onNewChat: (String) -> Unit,
    onOpenRemote: () -> Unit,
    onBrowserTask: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
        SectionHeader("快速开始")
        WorkGroup {
            QuickRow(Icons.Outlined.AddComment, "新建工作", "从一个目标开始，进度会持续保留") { onNewChat("") }
            Hairline()
            QuickRow(Icons.Outlined.Language, "浏览器调研", "让电脑读取网页并带回可核对来源", onBrowserTask)
            Hairline()
            QuickRow(Icons.Outlined.Sync, "设备接力", "把当前工作安全地交给在线电脑", onOpenRemote)
        }
    }
}

@Composable
private fun QuickRow(icon: ImageVector, title: String, subtitle: String, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, modifier = Modifier.size(21.dp))
        Spacer(Modifier.width(13.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(subtitle, fontSize = 11.5.sp, color = cs.onSurfaceVariant)
        }
        Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = cs.onSurfaceVariant, modifier = Modifier.size(18.dp))
    }
}

@Composable
private fun MoreRow(label: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, Modifier.weight(1f), fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.primary)
        Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(17.dp))
    }
}

@Composable
private fun Hairline() {
    Box(
        Modifier.fillMaxWidth().padding(start = 65.dp).height(0.5.dp)
            .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.65f)),
    )
}

private fun workKind(kind: String): String = when (kind) {
    "chat" -> "对话工作"
    "loop" -> "持续任务"
    "team" -> "协作任务"
    "browser" -> "浏览器调研"
    "computer" -> "电脑任务"
    "remote" -> "设备接力"
    "artifact" -> "文档成果"
    else -> "工作"
}

private fun workIcon(kind: String): ImageVector = when (kind) {
    "browser" -> Icons.Outlined.Language
    "computer", "remote" -> Icons.Outlined.Computer
    "artifact" -> Icons.Outlined.Description
    "loop" -> Icons.Outlined.Schedule
    else -> Icons.Outlined.FolderOpen
}

private fun routineAction(action: String): String = when (action) {
    "daily_brief" -> "每日工作简报"
    "corpus_digest" -> "资料更新摘要"
    "kg_health" -> "知识健康检查"
    else -> "自动工作"
}

private fun formatAgo(epoch: Double): String {
    if (epoch <= 0) return "刚刚"
    val millis = if (epoch > 1_000_000_000_000.0) epoch.toLong() else (epoch * 1000).toLong()
    val seconds = ((System.currentTimeMillis() - millis) / 1000).coerceAtLeast(0)
    return when {
        seconds < 60 -> "刚刚"
        seconds < 3600 -> "${seconds / 60} 分钟前"
        seconds < 86400 -> "${seconds / 3600} 小时前"
        else -> "${seconds / 86400} 天前"
    }
}

private fun formatTime(epochSeconds: Long): String {
    if (epochSeconds <= 0) return "待安排"
    val time = Instant.ofEpochSecond(epochSeconds).atZone(ZoneId.systemDefault())
    val today = LocalDate.now()
    val prefix = when (time.toLocalDate()) {
        today -> "今天"
        today.plusDays(1) -> "明天"
        else -> time.format(DateTimeFormatter.ofPattern("M月d日"))
    }
    return "$prefix ${time.format(DateTimeFormatter.ofPattern("HH:mm"))}"
}
