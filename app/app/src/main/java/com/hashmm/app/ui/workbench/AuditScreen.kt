package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AdminPanelSettings
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.Box
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.hashmm.app.data.remote.AuditItem
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import kotlinx.coroutines.launch

/** V221 权限审计（Kit 重铸）：概览三格 + 工具筛选 chips + 行卡。双端点回退防 422。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AuditScreen(onBack: () -> Unit, viewModel: AdminToolsViewModel = hiltViewModel()) {
    val scope = rememberCoroutineScope()
    var items by remember { mutableStateOf(WorkbenchCache.audit) }   // V275 跨导航持久化
    var error by remember { mutableStateOf<String?>(null) }
    var refreshing by remember { mutableStateOf(false) }
    var selTool by remember { mutableStateOf("") }
    // V228 高级筛选（直连 /audit/query）：用户名 + 时间窗
    var advOpen by remember { mutableStateOf(false) }
    var qUser by remember { mutableStateOf("") }
    var qDays by remember { mutableStateOf(0) }   // 0=全部 1=今天 7=近7天
    var filtered by remember { mutableStateOf(false) }
    fun runQuery() {
        scope.launch {
            refreshing = true
            val start = if (qDays > 0) System.currentTimeMillis() / 1000 - qDays * 86400L else 0L
            val (l2, e2) = viewModel.auditQuery(qUser.trim(), start)
            items = l2; error = e2; filtered = true; refreshing = false
        }
    }
    fun reload() { scope.launch { refreshing = true; val (l, e) = viewModel.audit(); items = l; WorkbenchCache.audit = l; error = e; refreshing = false } }
    LaunchedEffect(Unit) { if (WorkbenchCache.audit == null) reload() }
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        // V245：筛选入口收进页头右侧（Tune 图标），不再用一行孤零零的文字按钮
        ModuleHeader(Icons.Outlined.AdminPanelSettings, "权限审计", "谁在何时用了什么工具（与电脑端同源）", onBack,
            trailing = {
                IconButton(onClick = { advOpen = !advOpen }) {
                    Icon(Icons.Outlined.Tune, contentDescription = "高级筛选",
                        tint = if (advOpen || filtered) MaterialTheme.colorScheme.onSurface
                               else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(21.dp))
                }
            })
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            val l = items
            when {
                l == null && error == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                error != null -> HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = error!!, onRetry = { reload() })
                l!!.isEmpty() -> HmmStateView(kind = HmmStateKind.Empty, icon = Icons.Outlined.AdminPanelSettings,
                    title = "暂无审计记录", message = "Agent 每次调用工具都会留痕；需开启 HASHMM_AUDIT_TOOLS=1（默认开）")
                else -> Column(Modifier.fillMaxSize()) {
                    val tools = l!!.map { it.tool }.filter { it.isNotBlank() }.distinct()
                    // ── V245 高级筛选面板：白卡（浅灰容器输入 + 时间 chips + 墨黑查询键） ──
                    if (advOpen) {
                        Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp),
                            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                            Column(Modifier.padding(14.dp)) {
                                Surface(color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                                    shape = RoundedCornerShape(12.dp)) {
                                    Box(Modifier.fillMaxWidth().padding(horizontal = 13.dp, vertical = 11.dp)) {
                                        if (qUser.isEmpty()) Text("按用户名筛（留空＝全部）", fontSize = 13.sp,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.8f))
                                        androidx.compose.foundation.text.BasicTextField(
                                            value = qUser, onValueChange = { qUser = it }, singleLine = true,
                                            textStyle = androidx.compose.ui.text.TextStyle(fontSize = 13.sp,
                                                color = MaterialTheme.colorScheme.onSurface),
                                            cursorBrush = androidx.compose.ui.graphics.SolidColor(MaterialTheme.colorScheme.onSurface),
                                            modifier = Modifier.fillMaxWidth(),
                                        )
                                    }
                                }
                                Spacer(Modifier.height(10.dp))
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                                    listOf(0 to "全部时间", 1 to "今天", 7 to "近7天").forEach { (dd, label) ->
                                        val sel = qDays == dd
                                        Surface(
                                            color = if (sel) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                                            shape = RoundedCornerShape(50), modifier = Modifier.clip(RoundedCornerShape(50)).clickable { qDays = dd },
                                        ) { Text(label, fontSize = 12.sp,
                                            fontWeight = if (sel) FontWeight.SemiBold else FontWeight.Normal,
                                            color = if (sel) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant,
                                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)) }
                                    }
                                    Spacer(Modifier.weight(1f))
                                    if (filtered) Text("清空", fontSize = 12.5.sp, fontWeight = FontWeight.Medium,
                                        color = com.hashmm.app.ui.theme.BrandRed,
                                        modifier = Modifier.clip(RoundedCornerShape(50))
                                            .clickable { qUser = ""; qDays = 0; filtered = false; reload() }
                                            .padding(horizontal = 8.dp, vertical = 6.dp))
                                    Surface(color = MaterialTheme.colorScheme.primary, shape = RoundedCornerShape(50),
                                        modifier = Modifier.clip(RoundedCornerShape(50)).clickable { runQuery() }) {
                                        Text("查询", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold,
                                            color = MaterialTheme.colorScheme.onPrimary,
                                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 7.dp))
                                    }
                                }
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                    }
                    StatTriple("记录" to "${l!!.size}", "工具种类" to "${tools.size}",
                        "最新" to (kitAgo(l!!.maxOfOrNull { it.tsSec } ?: 0L).ifBlank { "—" }))
                    if (tools.size > 1) {
                        Row(
                            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                                .padding(horizontal = 16.dp, vertical = 8.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            (listOf("") + tools.take(8)).forEach { t ->
                                val sel = selTool == t
                                // V244：选中＝墨黑实心 + 白字（Marvis 分类 chip），未选中＝白底灰字
                                Surface(
                                    color = if (sel) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surface,
                                    shape = RoundedCornerShape(50),
                                    modifier = Modifier.clip(RoundedCornerShape(50)).clickable { selTool = t },
                                ) {
                                    Text(if (t.isBlank()) "全部" else t, fontSize = 12.sp,
                                        fontWeight = if (sel) FontWeight.SemiBold else FontWeight.Normal,
                                        color = if (sel) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
                                }
                            }
                        }
                    } else Spacer(Modifier.height(8.dp))
                    val shown = if (selTool.isBlank()) l!! else l!!.filter { it.tool == selTool }
                    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                        // V244：从"每行一张卡"收进一张分组白卡 + 发丝线
                        item {
                            ModuleSectionLabel("调用记录", "工具、操作者、风险与时间")
                            KitGroup {
                                shown.forEachIndexed { i, e ->
                                    if (i > 0) KitInsetDivider()
                                    // V245：图标按工具语义映射（搜索/文档/文件树/截图/浏览器…），不再一水儿终端图标
                                    KitRow(
                                        icon = auditToolIcon(e.tool), title = e.tool,
                                        sub = if (e.user.isNotBlank()) "操作者：${e.user}" else "",
                                        tone = KitTone.Primary, right = kitAgo(e.tsSec),
                                    )
                                }
                            }
                        }
                        item { Spacer(Modifier.height(12.dp)) }
                    }
                }
            }
        }
    }
}

/** V245 工具名 → 图标语义映射（未识别回退终端图标）。 */
private fun auditToolIcon(tool: String): androidx.compose.ui.graphics.vector.ImageVector {
    val t = tool.lowercase()
    return when {
        t.contains("search") || t.contains("query") -> Icons.Outlined.Search
        t.contains("document") || t.contains("write") || t.contains("create_file") || t.contains("doc") -> Icons.Outlined.Description
        t.contains("file_tree") || t.contains("list") || t.contains("dir") || t.contains("folder") -> Icons.Outlined.FolderOpen
        t.contains("screenshot") || t.contains("image") || t.contains("photo") -> Icons.Outlined.Image
        t.contains("browser") || t.contains("url") || t.contains("web") || t.contains("http") -> Icons.Outlined.Public
        t.contains("file") || t.contains("read") -> Icons.Outlined.Description
        else -> Icons.Outlined.Terminal
    }
}
