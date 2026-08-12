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
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Send
import androidx.compose.material.icons.outlined.Shield
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.hashmm.app.data.remote.AdvancedData
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import com.hashmm.app.ui.theme.AppType
import kotlinx.coroutines.launch

/** V219 高级能力（原生概览 v1，只读）：派活统计与最近任务 · 凭据 · 隔离 · 图库。
 *  操作（派活/保存凭据/放行）仍在电脑端；本页解决"随手看一眼"的高频诉求。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AdvancedScreen(onBack: () -> Unit, viewModel: AdminToolsViewModel = hiltViewModel()) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<AdvancedData?>(null) }
    var refreshing by remember { mutableStateOf(false) }
    fun reload() { scope.launch { refreshing = true; data = viewModel.advanced(); refreshing = false } }
    LaunchedEffect(Unit) { reload() }
    val d = data
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回") }
            Spacer(Modifier.width(4.dp))
            Column {
                Text("高级能力", style = AppType.screenTitle, color = MaterialTheme.colorScheme.onSurface)
                Text("派活 · 凭据 · 隔离 · 图库（只读概览，操作在电脑端）", style = AppType.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                d == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                d.error != null && d.dispatchStats.isEmpty() && d.credentials.isEmpty() && d.quarantineCount < 0 && d.imageCount < 0 ->
                    HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = d.error!!, onRetry = { reload() })
                else -> LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    item { SectionCard(Icons.Outlined.Send, "云上派活") {
                        val st = d.dispatchStats
                        Text(
                            if (st.isEmpty()) "队列为空"
                            else listOf("待认领 ${st["pending"] ?: 0}", "执行中 ${st["claimed"] ?: 0}",
                                "完成 ${st["done"] ?: 0}", "失败 ${st["failed"] ?: 0}").joinToString(" · "),
                            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                    } }
                    if (d.recentTasks.isNotEmpty()) {
                        itemsIndexed(d.recentTasks, key = { i, _ -> "t$i" }) { _, t ->
                            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(13.dp), modifier = Modifier.fillMaxWidth()) {
                                Row(Modifier.padding(horizontal = 14.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Column(Modifier.weight(1f)) {
                                        Text(t.kind, fontSize = 13.5.sp, fontWeight = FontWeight.Medium,
                                            color = MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                        Spacer(Modifier.height(2.dp))
                                        Text(listOf(t.status, t.runner, fmtAgoNative(t.createdSec)).filter { it.isNotBlank() }.joinToString(" · "),
                                            fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                }
                            }
                        }
                    }
                    item { SectionCard(Icons.Outlined.Key, "凭据仓") {
                        Text(
                            if (d.credentials.isEmpty()) "还没有凭据（电脑端「高级能力·凭据仓」添加）"
                            else "已存 ${d.credentials.size} 个：" + d.credentials.joinToString("、"),
                            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                    } }
                    item { SectionCard(Icons.Outlined.Shield, "入库质量闸") {
                        Text(
                            when {
                                d.quarantineCount < 0 -> "接口不可用"
                                d.quarantineCount == 0 -> "隔离区为空 · 坏文档不会污染检索索引"
                                else -> "待复核 ${d.quarantineCount} 篇" +
                                    (if (d.quarantineTop.isNotEmpty()) "：" + d.quarantineTop.joinToString("、") else "") +
                                    "（电脑端放行/拒绝）"
                            }, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                    } }
                    item { SectionCard(Icons.Outlined.Image, "会话图库") {
                        Text(
                            if (d.imageCount < 0) "接口不可用"
                            else "已入库 ${d.imageCount} 张 · ${fmtBytesNative(d.imageBytes)}（对话/上传图片自动入库，Agent 可检索复用）",
                            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                    } }
                    item { Spacer(Modifier.height(12.dp)) }
                }
            }
        }
    }
}

@Composable
private fun SectionCard(icon: ImageVector, title: String, content: @Composable () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(17.dp))
                Spacer(Modifier.width(9.dp))
                Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
            }
            Spacer(Modifier.height(7.dp))
            content()
        }
    }
}

internal fun fmtBytesNative(b: Long): String = when {
    b >= 1_073_741_824 -> String.format("%.1fGB", b / 1_073_741_824.0)
    b >= 1_048_576 -> String.format("%.1fMB", b / 1_048_576.0)
    b >= 1024 -> String.format("%.0fKB", b / 1024.0)
    else -> "${b}B"
}
