package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Send
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.hashmm.app.data.remote.AdvancedData
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import kotlinx.coroutines.launch

/** V219 高级能力（原生概览 v1，只读）：派活统计与最近任务 · 凭据 · 隔离 · 图库。
 *  操作（派活/保存凭据/放行）仍在电脑端；本页解决"随手看一眼"的高频诉求。 */
@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun AdvancedScreen(
    onBack: () -> Unit,
    onOpenTaskLauncher: (String, String) -> Unit = { _, _ -> },
    onOpenAgentsStudio: () -> Unit = {},
    viewModel: AdminToolsViewModel = hiltViewModel(),
) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf(WorkbenchCache.advanced) }   // V275 跨导航持久化
    var refreshing by remember { mutableStateOf(false) }
    var lastMsg by remember { mutableStateOf("") }
    fun reload() { scope.launch { refreshing = true; data = viewModel.advanced().also { WorkbenchCache.advanced = it }; refreshing = false } }
    LaunchedEffect(Unit) { if (WorkbenchCache.advanced == null) reload() }
    val d = data
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        ModuleHeader(Icons.Outlined.Send, "高级能力", "派活 · 凭据 · 隔离 · 图库（轻操作，管理在电脑端）", onBack)
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                d == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                d.error != null && d.dispatchStats.isEmpty() && d.credentials.isEmpty() && d.quarantineCount < 0 && d.imageCount < 0 ->
                    HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = d.error!!, onRetry = { reload() })
                else -> LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    // ── V245 对齐桌面端「云上派活」信息层次：队列四格概览 → 派一个任务 → 最近任务 ──
                    item {
                        ModuleSectionLabel("任务队列", "派发、认领、执行与回填")
                        QueueOverview(d.dispatchStats)
                    }
                    item { SectionCard(Icons.Outlined.Send, "派一个任务") {
                        Text("任务派给桌面端 / 任意 runner 认领执行，结果回填到会话",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 16.sp)
                        Spacer(Modifier.height(9.dp))
                        androidx.compose.foundation.layout.FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            ActionPill("浏览器查") { onOpenTaskLauncher("browser", "") }
                            ActionPill("取文件") { onOpenTaskLauncher("file", "") }
                            ActionPill("多步规划") { onOpenTaskLauncher("seq", "") }
                            ActionPill("只读检查") { onOpenTaskLauncher("cu", "") }
                            ActionPill("多智能体") { onOpenAgentsStudio() }
                        }
                        if (lastMsg.isNotBlank()) {
                            Spacer(Modifier.height(7.dp))
                            Text(lastMsg, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    } }
                    if (d.recentTasks.isNotEmpty()) {
                        item { Text("最近任务", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold,
                            letterSpacing = 0.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = 2.dp, top = 4.dp)) }
                        // V245：状态改徽章（完成绿 / 失败红 / 执行中琥珀），与桌面端最近任务行同语言
                        item {
                            KitGroup {
                                d.recentTasks.forEachIndexed { i, t ->
                                    if (i > 0) KitInsetDivider()
                                    val bt = when {
                                        t.status.contains("done", true) -> KitTone.Success
                                        t.status.contains("fail", true) -> KitTone.Error
                                        t.status.contains("claim", true) -> KitTone.Warn
                                        else -> KitTone.Neutral
                                    }
                                    KitRow(
                                        icon = Icons.Outlined.Send, title = t.kind,
                                        sub = if (t.runner.isNotBlank()) "→ ${t.runner}" else "",
                                        badge = t.status.ifBlank { "排队" }, badgeTone = bt,
                                        right = kitAgo(t.createdSec),
                                    )
                                }
                            }
                        }
                    }
                    item { ModuleSectionLabel("治理与资产", "凭据、入库、图库和远程能力") }
                    item { SectionCard(Icons.Outlined.Key, "凭据仓") {
                        Text(
                            if (d.credentials.isEmpty()) "还没有凭据（电脑端「高级能力·凭据仓」添加）"
                            else "已存 ${d.credentials.size} 个：" + d.credentials.joinToString("、"),
                            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                    } }
                    item { SectionCard(Icons.Outlined.Shield, "入库质量闸",
                        badge = if (d.quarantineCount > 0) "${d.quarantineCount}" else "",
                        badgeTone = KitTone.Warn) {
                        Text(
                            when {
                                d.quarantineCount < 0 -> "接口不可用"
                                d.quarantineCount == 0 -> "隔离区为空 · 坏文档不会污染检索索引"
                                else -> "待复核 ${d.quarantineCount} 篇" +
                                    (if (d.quarantineTop.isNotEmpty()) "：" + d.quarantineTop.joinToString("、") else "") +
                                    "（电脑端放行/拒绝）"
                            }, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                    } }
                    item { SectionCard(Icons.Outlined.Image, "会话图库",
                        badge = if (d.imageCount >= 0) "${d.imageCount}" else "",
                        badgeTone = KitTone.Neutral) {
                        Text(
                            if (d.imageCount < 0) "接口不可用"
                            else "已入库 ${d.imageCount} 张 · ${fmtBytesNative(d.imageBytes)}（对话/上传图片自动入库，Agent 可检索复用）",
                            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                    } }
                    item { SectionCard(Icons.Outlined.Send, "电脑控制", badge = "远程", badgeTone = KitTone.Primary) {
                        Text("防休眠与截屏屏幕——电脑端在线即刻生效（电脑托盘菜单也可改）",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(9.dp))
                        androidx.compose.foundation.layout.FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            ActionPill("开防休眠") { scope.launch {
                                val (okd, msg) = viewModel.dispatch("keep_awake", org.json.JSONObject().put("on", true))
                                lastMsg = if (okd) "已让电脑保持唤醒（不休眠）" else msg } }
                            ActionPill("关防休眠") { scope.launch {
                                val (okd, msg) = viewModel.dispatch("keep_awake", org.json.JSONObject().put("on", false))
                                lastMsg = if (okd) "已恢复系统休眠策略" else msg } }
                            ActionPill("切主屏") { scope.launch {
                                val (_, msg) = viewModel.dispatch("set_display", org.json.JSONObject())
                                lastMsg = msg } }
                            ActionPill("切副屏") { scope.launch {
                                val (_, msg) = viewModel.dispatch("set_display", org.json.JSONObject().put("index", 1))
                                lastMsg = msg } }
                        }
                    } }
                    item { SectionCard(Icons.Outlined.Description, "文件夹哨兵", badge = "自动", badgeTone = KitTone.Success) {
                        Text("电脑上这个目录一有新文件 → 自动 POST 触发 URL（在桌面「高级能力·触发器」创建并复制）",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(6.dp))
                        var sentryDir by remember { mutableStateOf("") }
                        var sentryUrl by remember { mutableStateOf("") }
                        OutlinedTextField(value = sentryDir, onValueChange = { sentryDir = it }, singleLine = true,
                            placeholder = { Text("电脑目录，如 D:\\下载", fontSize = 12.sp) }, modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(6.dp))
                        OutlinedTextField(value = sentryUrl, onValueChange = { sentryUrl = it }, singleLine = true,
                            placeholder = { Text("触发 URL（https://…/api/hooks/t/…）", fontSize = 12.sp) }, modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(9.dp))
                        androidx.compose.foundation.layout.FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            ActionPill("上岗", enabled = sentryDir.isNotBlank() && sentryUrl.startsWith("http")) { scope.launch {
                                val (_, msg) = viewModel.dispatch("watch_dir",
                                    org.json.JSONObject().put("dir", sentryDir.trim()).put("url", sentryUrl.trim()))
                                lastMsg = msg
                            } }
                            ActionPill("全部撤岗") { scope.launch {
                                val (_, msg) = viewModel.dispatch("watch_stop", org.json.JSONObject().put("all", true))
                                lastMsg = msg
                            } }
                            ActionPill("查看哨兵") { scope.launch {
                                val (okd, msg) = viewModel.dispatch("watch_list", org.json.JSONObject())
                                lastMsg = if (okd) msg else msg
                            } }
                        }
                    } }
                    item { Spacer(Modifier.height(12.dp)) }
                }
            }
        }
    }
}

/** V245 队列四格概览：待认领 / 执行中(琥珀) / 完成(绿) / 失败(红)——对齐桌面端派活页的概览条。 */
@Composable
private fun QueueOverview(st: Map<String, Int>) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surface, shape = RoundedCornerShape(18.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(vertical = 15.dp), verticalAlignment = Alignment.CenterVertically) {
            val cells = listOf(
                Triple("待认领", st["pending"] ?: 0, cs.onSurface),
                Triple("执行中", st["claimed"] ?: 0, androidx.compose.ui.graphics.Color(0xFFB45309)),
                Triple("完成", st["done"] ?: 0, androidx.compose.ui.graphics.Color(0xFF15803D)),
                Triple("失败", st["failed"] ?: 0, com.hashmm.app.ui.theme.BrandRed),
            )
            cells.forEachIndexed { i, (label, v, c) ->
                Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("$v", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold,
                        color = if (v > 0) c else cs.onSurfaceVariant.copy(alpha = 0.55f), letterSpacing = (-0.5).sp)
                    Spacer(Modifier.height(2.dp))
                    Text(label, fontSize = 11.sp, color = cs.onSurfaceVariant)
                }
                if (i < cells.size - 1) Box(Modifier.width(1.dp).height(26.dp)
                    .background(cs.outlineVariant.copy(alpha = 0.5f)))
            }
        }
    }
}

@Composable
private fun SectionCard(icon: ImageVector, title: String, badge: String = "", badgeTone: KitTone = KitTone.Neutral, content: @Composable () -> Unit) {
    // V244：灰底功能卡 → 无边白卡；图标裸放墨黑（全 App 同规）
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(18.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 15.dp, vertical = 14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(36.dp).background(
                        MaterialTheme.colorScheme.surfaceVariant,
                        RoundedCornerShape(11.dp),
                    ),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(icon, null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(18.dp))
                }
                Spacer(Modifier.width(12.dp))
                Text(title, fontSize = 14.5.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface,
                    letterSpacing = (-0.2).sp, modifier = Modifier.weight(1f))
                if (badge.isNotBlank()) StatusBadge(badge, badgeTone)
            }
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

/** V244 操作胶囊：替代散落的 TextButton——浅灰底 + 墨黑字，小而克制。 */
@Composable
private fun ActionPill(label: String, enabled: Boolean = true, onClick: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = if (enabled) 0.65f else 0.35f),
        shape = RoundedCornerShape(50),
        modifier = Modifier.clip(RoundedCornerShape(50)).clickable(enabled = enabled, onClick = onClick),
    ) {
        Text(label, fontSize = 12.sp, fontWeight = FontWeight.Medium,
            color = MaterialTheme.colorScheme.onSurface.copy(alpha = if (enabled) 1f else 0.45f),
            modifier = Modifier.padding(horizontal = 13.dp, vertical = 7.dp))
    }
}

internal fun fmtBytesNative(b: Long): String = when {
    b >= 1_073_741_824 -> String.format("%.1fGB", b / 1_073_741_824.0)
    b >= 1_048_576 -> String.format("%.1fMB", b / 1_048_576.0)
    b >= 1024 -> String.format("%.0fKB", b / 1024.0)
    else -> "${b}B"
}
