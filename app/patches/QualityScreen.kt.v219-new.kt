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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.BarChart
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.hashmm.app.data.remote.QualityData
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import com.hashmm.app.ui.theme.AppType
import kotlinx.coroutines.launch

/** V219 质量看板（原生）：/api/admin/quality/dashboard 关键指标（字段后端原样，不造数）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QualityScreen(onBack: () -> Unit, viewModel: AdminToolsViewModel = hiltViewModel()) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<QualityData?>(null) }
    var refreshing by remember { mutableStateOf(false) }
    fun reload() { scope.launch { refreshing = true; data = viewModel.quality(); refreshing = false } }
    LaunchedEffect(Unit) { reload() }
    val d = data
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回") }
            Spacer(Modifier.width(4.dp))
            Column {
                Text("质量看板", style = AppType.screenTitle, color = MaterialTheme.colorScheme.onSurface)
                Text("近 7 天关键指标（与电脑端同源）", style = AppType.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                d == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                d.error != null -> HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = d.error!!, onRetry = { reload() })
                d.stats.isEmpty() -> HmmStateView(kind = HmmStateKind.Empty, icon = Icons.Outlined.BarChart,
                    title = "暂无指标", message = "跑几轮问答/评测后这里会出现质量数据")
                else -> LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                    itemsIndexed(d.stats, key = { i, _ -> "q$i" }) { _, (k, v) ->
                        Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(13.dp), modifier = Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(k, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                                Text(v, fontSize = 15.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
                            }
                        }
                    }
                    item { Spacer(Modifier.height(12.dp)) }
                }
            }
        }
    }
}
