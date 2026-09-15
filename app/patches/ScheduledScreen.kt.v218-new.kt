package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.hashmm.app.data.remote.FeedData
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import com.hashmm.app.ui.theme.AppType
import kotlinx.coroutines.launch

/** V218 定时任务（原生）：与桌面端同源数据（/api/feed.scheduled，管理员见明细≤6）。
 *  用户裁定 WebView 硬搬≠适配——本页按 App 设计语言重做，数据同源、体验原生。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScheduledScreen(onBack: () -> Unit, viewModel: NativeFeedViewModel = hiltViewModel()) {
    val scope = rememberCoroutineScope()
    var feed by remember { mutableStateOf<FeedData?>(null) }
    var refreshing by remember { mutableStateOf(false) }
    fun reload() { scope.launch { refreshing = true; feed = viewModel.load(); refreshing = false } }
    LaunchedEffect(Unit) { reload() }
    val f = feed
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回") }
            Spacer(Modifier.width(4.dp))
            Column {
                Text("定时任务", style = AppType.screenTitle, color = MaterialTheme.colorScheme.onSurface)
                Text("主动服务 · 最近结果（与电脑端同源）", style = AppType.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                f == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                f.error != null -> HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = f.error!!, onRetry = { reload() })
                f.scheduled.isEmpty() && f.scheduledCount <= 0 -> HmmStateView(
                    kind = HmmStateKind.Empty, icon = Icons.Outlined.Schedule,
                    title = "还没有定时任务",
                    message = if (f.scheduledCount == 0) "在电脑端「定时任务」里创建，让系统按时主动干活" else "明细需要管理员账号（普通账号仅见数量）",
                )
                else -> LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    itemsIndexed(f.scheduled, key = { i, _ -> "s$i" }) { _, s ->
                        Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Outlined.Schedule, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(12.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(s.name + if (!s.enabled) "（已停用）" else "", fontSize = 14.5.sp, fontWeight = FontWeight.Medium,
                                        color = MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    val sub = listOf(fmtAgoNative(s.lastRunSec), s.lastResult).filter { it.isNotBlank() }.joinToString(" · ")
                                    if (sub.isNotBlank()) {
                                        Spacer(Modifier.height(2.dp))
                                        Text(sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                    }
                                }
                            }
                        }
                    }
                    item { Spacer(Modifier.height(12.dp)) }
                }
            }
        }
    }
}

internal fun fmtAgoNative(sec: Long): String {
    if (sec <= 0) return ""
    val d = (System.currentTimeMillis() / 1000 - sec).coerceAtLeast(0)
    return when {
        d < 60 -> "刚刚"; d < 3600 -> "${d / 60}分钟前"; d < 86400 -> "${d / 3600}小时前"; else -> "${d / 86400}天前"
    }
}
