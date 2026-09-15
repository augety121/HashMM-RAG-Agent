package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.PauseCircleOutline
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.PlayCircleOutline
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import com.hashmm.app.data.remote.FeedData
import com.hashmm.app.data.remote.ScheduledData
import com.hashmm.app.data.remote.ScheduledTask
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import kotlinx.coroutines.launch

/** 普通用户例行任务：使用 owner-scoped API，并把结果写回绑定的 Chat。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScheduledScreen(onBack: () -> Unit, viewModel: NativeFeedViewModel = hiltViewModel()) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<ScheduledData?>(null) }
    var feed by remember { mutableStateOf<FeedData?>(null) }
    var refreshing by remember { mutableStateOf(false) }
    var showCreate by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf<ScheduledTask?>(null) }
    var openTaskMenu by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }

    fun reload() {
        scope.launch {
            refreshing = true
            data = viewModel.scheduled()
            feed = viewModel.load()
            refreshing = false
        }
    }
    fun mutate(block: suspend () -> String?) {
        scope.launch {
            val error = block()
            message = error ?: "操作已完成"
            data = viewModel.scheduled()
        }
    }
    LaunchedEffect(Unit) { reload() }

    val d = data
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        ModuleHeader(Icons.Outlined.Schedule, "定时完成", "到时间自动执行，结果回到创建时绑定的对话", onBack) {
            IconButton(onClick = { showCreate = true }, enabled = d?.actions?.isNotEmpty() == true) {
                Icon(Icons.Outlined.Add, contentDescription = "新建定时任务")
            }
        }
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                d == null -> HmmStateView(kind = HmmStateKind.Loading, title = "正在读取定时任务")
                d.error != null -> HmmStateView(
                    kind = HmmStateKind.Error, title = "无法读取定时任务", message = d.error,
                    onRetry = { reload() },
                )
                else -> Column(Modifier.fillMaxSize()) {
                    StatTriple(
                        "任务" to d.tasks.size.toString(),
                        "启用" to d.tasks.count { it.enabled }.toString(),
                        "调度器" to if (d.schedulerEnabled) "运行中" else "未启动",
                        toneB = if (d.schedulerEnabled) KitTone.Success else KitTone.Warn,
                    )
                    Spacer(Modifier.height(10.dp))
                    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                        if (!d.schedulerEnabled) item {
                            ModuleSectionLabel("服务状态", "任务仍可管理和立即运行")
                            KitGroup {
                                MetricRow(
                                    "自动执行尚未启用",
                                    "仍可立即运行",
                                    "服务管理员启用自动执行后，无需保持手机或桌面端打开",
                                    KitTone.Warn,
                                )
                            }
                            Spacer(Modifier.height(12.dp))
                        }
                        message?.let { text ->
                            item {
                                KitGroup { MetricRow("最近操作", text, "来自真实接口返回", if (text == "操作已完成") KitTone.Success else KitTone.Warn) }
                                Spacer(Modifier.height(12.dp))
                            }
                        }
                        item { ModuleSectionLabel("任务", "结果会回写创建时绑定的 Chat") }
                        if (d.tasks.isEmpty()) item {
                            KitGroup {
                                Text(
                                    "当前没有定时任务。点击右上角加号创建；App 会把任务绑定到最近一次对话，后续结果可直接回到 Chat。",
                                    fontSize = 13.sp,
                                    lineHeight = 19.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(16.dp),
                                )
                            }
                        } else item {
                            KitGroup {
                                d.tasks.forEachIndexed { index, task ->
                                    if (index > 0) KitInsetDivider()
                                    val schedule = if (task.scheduleKind == "daily") "每天 ${task.dailyAt}"
                                        else "每 ${task.intervalSeconds / 3600.0} 小时"
                                    val sub = listOf(
                                        task.action,
                                        schedule,
                                        task.lastResult.takeIf { it.isNotBlank() },
                                    ).filterNotNull().joinToString(" · ")
                                    KitRow(
                                        icon = Icons.Outlined.Schedule,
                                        title = task.name,
                                        sub = sub,
                                        badge = if (task.enabled) "启用" else "暂停",
                                        badgeTone = if (task.enabled) KitTone.Success else KitTone.Neutral,
                                        right = kitAgo(task.lastRunSec),
                                    trailing = {
                                        Row {
                                            IconButton(onClick = { mutate { viewModel.runScheduled(task.id) } }) {
                                                Icon(Icons.Outlined.PlayArrow, "立即运行")
                                            }
                                            Box {
                                                IconButton(onClick = { openTaskMenu = task.id }) {
                                                    Icon(Icons.Outlined.MoreVert, "更多操作")
                                                }
                                                DropdownMenu(
                                                    expanded = openTaskMenu == task.id,
                                                    onDismissRequest = { openTaskMenu = "" },
                                                ) {
                                                    DropdownMenuItem(
                                                        text = { Text(if (task.enabled) "暂停自动执行" else "恢复自动执行") },
                                                        leadingIcon = { Icon(if (task.enabled) Icons.Outlined.PauseCircleOutline else Icons.Outlined.PlayCircleOutline, null) },
                                                        onClick = {
                                                            openTaskMenu = ""
                                                            mutate { viewModel.toggleScheduled(task.id, !task.enabled) }
                                                        },
                                                    )
                                                    DropdownMenuItem(
                                                        text = { Text("删除任务") },
                                                        leadingIcon = { Icon(Icons.Outlined.DeleteOutline, null) },
                                                        onClick = { openTaskMenu = ""; deleting = task },
                                                    )
                                                }
                                            }
                                        }
                                    },
                                    )
                                }
                            }
                        }
                        item { Spacer(Modifier.height(18.dp)) }
                    }
                }
            }
        }
    }

    if (showCreate && d != null) {
        CreateScheduledDialog(
            actions = d.actions,
            targetChat = feed?.convs?.firstOrNull()?.title.orEmpty(),
            onDismiss = { showCreate = false },
            onCreate = { action, name, hours ->
                showCreate = false
                mutate { viewModel.createScheduled(action, name, hours, feed?.convs?.firstOrNull()?.id.orEmpty()) }
            },
        )
    }
    deleting?.let { task ->
        AlertDialog(
            onDismissRequest = { deleting = null },
            title = { Text("删除定时任务") },
            text = { Text("将删除“${task.name}”。历史 Chat 结果不会被删除。") },
            confirmButton = { TextButton(onClick = { deleting = null; mutate { viewModel.deleteScheduled(task.id) } }) { Text("删除") } },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("取消") } },
        )
    }
}

@Composable
private fun CreateScheduledDialog(
    actions: List<String>,
    targetChat: String,
    onDismiss: () -> Unit,
    onCreate: (String, String, Int) -> Unit,
) {
    var selected by remember(actions) { mutableStateOf(actions.firstOrNull().orEmpty()) }
    var name by remember { mutableStateOf("") }
    var hoursText by remember { mutableStateOf("24") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("新建定时任务") },
        text = {
            Column {
                Text("动作由服务器注册表提供，不在 App 中伪造。", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                Spacer(Modifier.height(10.dp))
                LazyRow { items(actions) { action ->
                    FilterChip(
                        selected = selected == action,
                        onClick = { selected = action },
                        label = { Text(action) },
                        modifier = Modifier.padding(end = 6.dp),
                    )
                } }
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(name, { name = it }, label = { Text("任务名称") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(hoursText, { hoursText = it.filter(Char::isDigit).take(3) }, label = { Text("间隔小时") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                if (targetChat.isNotBlank()) {
                    Spacer(Modifier.height(8.dp))
                    Text("结果回写：$targetChat", fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = selected.isNotBlank() && (hoursText.toIntOrNull() ?: 0) > 0,
                onClick = { onCreate(selected, name, hoursText.toIntOrNull() ?: 24) },
            ) { Text("创建") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
