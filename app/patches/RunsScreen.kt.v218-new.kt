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
import androidx.compose.material.icons.outlined.Bolt
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

/** V218 运行轨迹（原生）：每次执行做了什么（/api/feed.runs，管理员≤5 条摘要）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RunsScreen(onBack: () -> Unit, viewModel: NativeFeedViewModel = hiltViewModel()) {
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
                Text("运行轨迹", style = AppType.screenTitle, color = MaterialTheme.colorScheme.onSurface)
                Text("每次执行做了什么（与电脑端同源）", style = AppType.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                f == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                f.error != null -> HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = f.error!!, onRetry = { reload() })
                f.runs.isEmpty() -> HmmStateView(kind = HmmStateKind.Empty, icon = Icons.Outlined.Bolt,
                    title = "还没有运行记录",
                    message = "跑一次 Agent 任务后这里会出现执行摘要（需管理员账号与后端 V223+）")
                else -> LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    itemsIndexed(f.runs, key = { i, _ -> "r$i" }) { _, r ->
                        Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Outlined.Bolt, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(17.dp))
                                Spacer(Modifier.width(12.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(r.title, fontSize = 14.sp, fontWeight = FontWeight.Medium,
                                        color = MaterialTheme.colorScheme.onSurface, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                    val sub = listOf(r.status, fmtAgoNative(r.tsSec)).filter { it.isNotBlank() }.joinToString(" · ")
                                    if (sub.isNotBlank()) {
                                        Spacer(Modifier.height(2.dp))
                                        Text(sub, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
