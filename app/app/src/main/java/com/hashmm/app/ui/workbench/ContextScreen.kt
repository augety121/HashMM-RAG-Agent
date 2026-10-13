package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material.icons.outlined.CenterFocusStrong
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import com.hashmm.app.data.remote.AdminToolsRepository
import com.hashmm.app.data.remote.FeedConv
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import kotlinx.coroutines.launch

/** V268 上下文透视（对标桌面端 contextInspect / OpenClaw `/context list`）：
 *  一眼看清这轮对话的 system 上下文由哪些块组成——引导文件各级 / 长期记忆 /
 *  用户画像 / 会话补丁 / 动态块——每块字符数 + 预览 + 精简建议。
 *  排查"模型为何知道/不知道某事"、控上下文预算全靠它。只读观测，不改任何状态。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ContextScreen(
    onBack: () -> Unit,
    onOpenConversation: (String) -> Unit = {},
    viewModel: AdminToolsViewModel = hiltViewModel(),
    feedViewModel: NativeFeedViewModel = hiltViewModel(),
) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf(WorkbenchCache.context) }   // V275 跨导航持久化
    var refreshing by remember { mutableStateOf(false) }
    var expandedBlock by remember { mutableStateOf<String?>(null) }
    var recentConversations by remember { mutableStateOf<List<FeedConv>>(emptyList()) }
    var selectedConversation by rememberSaveable { mutableStateOf("") }
    var compacting by remember { mutableStateOf(false) }
    var compactMessage by remember { mutableStateOf("") }
    fun reload(convId: String = selectedConversation) {
        scope.launch {
            refreshing = true
            data = viewModel.contextInspect(convId).also { WorkbenchCache.context = it }
            refreshing = false
        }
    }
    fun compact(convId: String) {
        if (compacting || convId.isBlank()) return
        scope.launch {
            compacting = true
            val (ok, message) = viewModel.contextCompact(convId)
            compactMessage = message
            if (ok) data = viewModel.contextInspect(convId).also { WorkbenchCache.context = it }
            compacting = false
        }
    }
    LaunchedEffect(Unit) {
        recentConversations = feedViewModel.load().convs.take(12)
        if (WorkbenchCache.context == null) reload()
    }
    val d = data
    LaunchedEffect(d?.convId) {
        val projectedConversation = d?.convId.orEmpty()
        if (selectedConversation.isBlank() && projectedConversation.isNotBlank()) {
            selectedConversation = projectedConversation
        }
    }
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        ModuleHeader(Icons.Outlined.CenterFocusStrong, "上下文透视", "定位当前 Chat 实际可用的规则、记忆与会话补丁", onBack)
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload(selectedConversation) }, modifier = Modifier.fillMaxSize()) {
            when {
                d == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                d.error != null -> HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = d.error.orEmpty(), onRetry = { reload() })
                d.blocks.isEmpty() -> HmmStateView(kind = HmmStateKind.Empty, icon = Icons.Outlined.CenterFocusStrong,
                    title = "暂无数据", message = "后端过旧：unzip -o 新包并重启后端即可")
                else -> {
                    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                        item {
                            Spacer(Modifier.height(10.dp))
                            StatTriple("组成块" to d.blocks.size.toString(),
                                "注入总量" to "${d.totalChars} 字",
                                "在场块" to d.blocks.count { it.present }.toString(),
                                toneB = if (d.totalChars > 60000) KitTone.Warn else KitTone.Success)
                        }
                        if (recentConversations.isNotEmpty()) {
                            item {
                                Spacer(Modifier.height(10.dp))
                                ModuleSectionLabel("选择会话", "切换后读取该 Chat 的真实上下文")
                                LazyRow(horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp)) {
                                    items(recentConversations, key = { it.id }) { conv ->
                                        FilterChip(
                                            selected = selectedConversation == conv.id,
                                            onClick = {
                                                selectedConversation = conv.id
                                                expandedBlock = null
                                                reload(conv.id)
                                            },
                                            label = { Text(conv.title.ifBlank { "未命名对话" }, maxLines = 1) },
                                        )
                                    }
                                }
                            }
                        }
                        item {
                            Spacer(Modifier.height(10.dp))
                            ModuleInsight(
                                icon = Icons.Outlined.ChatBubbleOutline,
                                title = d.convTitle.ifBlank { "尚无可透视会话" },
                                body = when {
                                    d.convId.isBlank() -> "先在 Chat 中发起一次对话，系统才有真实上下文可以检查。"
                                    d.query.isNotBlank() -> "最近问题：${d.query.take(160)}"
                                    else -> "已读取该会话的规则、记忆与动态上下文。"
                                },
                                tone = if (d.convId.isBlank()) KitTone.Warn else KitTone.Primary,
                                badge = if (d.resolvedLatest) "最近会话" else "已选择",
                                actionLabel = if (d.convId.isNotBlank()) "打开该对话" else "",
                                onAction = if (d.convId.isNotBlank()) ({ onOpenConversation(d.convId) }) else null,
                            )
                        }
                        if (d.convId.isNotBlank()) {
                            item {
                                Spacer(Modifier.height(10.dp))
                                ModuleInsight(
                                    icon = Icons.Outlined.CenterFocusStrong,
                                    title = "长对话检查点",
                                    body = compactMessage.ifBlank {
                                        "自动压缩会在上下文接近预算时运行；也可以现在手动折叠早期消息。完整历史不会删除。"
                                    },
                                    tone = if (compactMessage.isBlank()) KitTone.Primary else KitTone.Success,
                                    badge = if (compacting) "处理中" else "可恢复",
                                    actionLabel = if (compacting) "" else "立即压缩",
                                    onAction = if (compacting) null else ({ compact(d.convId) }),
                                )
                            }
                        }
                        item {
                            Spacer(Modifier.height(10.dp))
                            ModuleSectionLabel("透视对象", if (d.resolvedLatest) "自动选择当前账号最近的 Chat" else "指定 Chat")
                            KitGroup {
                                MetricRow(
                                    "会话",
                                    d.convTitle.ifBlank { "尚无会话" },
                                    d.convId.ifBlank { "先在 Chat 中发起一次对话" },
                                    if (d.convId.isBlank()) KitTone.Warn else KitTone.Success,
                                )
                                if (d.query.isNotBlank()) {
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("最近用户问题", d.query.take(120), "项目规则按这条真实问题选择")
                                }
                            }
                        }
                        // 精简建议
                        if (d.tips.isNotEmpty()) {
                            item {
                                Spacer(Modifier.height(10.dp))
                                ModuleSectionLabel("精简建议", "降低噪声与上下文成本")
                                KitGroup {
                                    d.tips.forEachIndexed { i, t ->
                                        if (i > 0) KitInsetDivider(start = 15.dp)
                                        MetricRow(label = "建议", value = t,
                                            tone = if (t.contains("健康")) KitTone.Success else KitTone.Warn)
                                    }
                                }
                            }
                        }
                        // 各块:名称 + 字符数徽记 + 预览/说明
                        item {
                            Spacer(Modifier.height(12.dp))
                            ModuleSectionLabel("上下文组成", "点击展开正文；动态块会明确标为逐轮生成")
                            KitGroup {
                                d.blocks.forEachIndexed { i, b ->
                                    if (i > 0) KitInsetDivider(start = 15.dp)
                                    Row(Modifier.fillMaxWidth().clickable { expandedBlock = if (expandedBlock == b.id) null else b.id }
                                        .padding(horizontal = 14.dp, vertical = 10.dp),
                                        verticalAlignment = Alignment.CenterVertically) {
                                        Column(Modifier.weight(1f)) {
                                            Text(b.name, style = MaterialTheme.typography.bodyMedium,
                                                color = MaterialTheme.colorScheme.onSurface)
                                            val source = if (b.present && b.preview.isNotBlank()) b.preview else b.note
                                            val sub = if (expandedBlock == b.id) source else source.take(80)
                                            if (sub.isNotBlank()) {
                                                Text(sub, style = MaterialTheme.typography.bodySmall,
                                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                                    maxLines = if (expandedBlock == b.id) 12 else 2)
                                            }
                                        }
                                        Text(if (b.present) "${b.chars} 字" else "未注入",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = if (b.present) MaterialTheme.colorScheme.primary
                                                    else MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                }
                            }
                        }
                        item { Spacer(Modifier.height(16.dp)) }
                    }
                }
            }
        }
    }
}
