package com.hashmm.app.ui.relay
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DesktopWindows
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.SwapHoriz
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.sync.ChatConversation

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RelayScreen(onBack: () -> Unit, viewModel: RelayViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(ui.toast) { ui.toast?.let { snackbar.showSnackbar(it); viewModel.clearToast() } }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            ScreenHeader(title = "接力", onBack = onBack) {
                IconButton(onClick = { viewModel.load() }) { Icon(Icons.Outlined.Refresh, contentDescription = "刷新") }
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // 说明
            item {
                // V245：说明卡换品牌米色（与工作台「接力」英雄卡同一语气面色）
                Surface(color = com.hashmm.app.ui.theme.WarmBeige, shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(horizontal = 18.dp, vertical = 16.dp), verticalAlignment = Alignment.Top) {
                        Icon(Icons.Outlined.SwapHoriz, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(22.dp))
                        Spacer(Modifier.width(13.dp))
                        Column {
                            Text("设备间接力", fontSize = 15.5.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface, letterSpacing = (-0.2).sp)
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "对话在后端持续运行，关掉 app 也不会中断。需要合盖时把它交给桌面端继续，稍后再切回手机。",
                                fontSize = 12.5.sp, color = com.hashmm.app.ui.theme.OnWarmBeige, lineHeight = 18.sp,
                            )
                        }
                    }
                }
            }

            // 在线桌面主机
            item {
                Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.DesktopWindows, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(21.dp))
                        Spacer(Modifier.width(14.dp))
                        Column(Modifier.weight(1f)) {
                            val host = ui.hosts.firstOrNull()
                            Text(host?.name ?: "无在线桌面主机", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                            Text(
                                if (host != null) "在线 · 可接管对话" else "在电脑端登录同账号并开启远程后出现",
                                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (ui.hosts.isNotEmpty()) Box(Modifier.size(9.dp).clip(CircleShape).background(Color(0xFF34C759)))
                    }
                }
            }

            // 进行中的接力：周期状态跟踪（发送中 → 已送达 → 桌面已接管 / 失败）
            if (ui.statuses.isNotEmpty()) {
                item {
                    Text("进行中的接力", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 2.dp))
                }
                items(ui.statuses.values.sortedByDescending { it.startedAt }, key = { "st-" + it.convId }) { st ->
                    val conv = ui.conversations.firstOrNull { it.id == st.convId }
                    RelayStatusCard(
                        title = conv?.title?.ifBlank { "对话" } ?: "对话",
                        status = st,
                        onDismiss = { viewModel.dismissStatus(st.convId) },
                    )
                }
            }

            item {
                Text("选择对话交接", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp, start = 2.dp))
            }

            if (ui.loading && ui.conversations.isEmpty()) {
                item { com.hashmm.app.ui.components.HmmSkeletonList(count = 5) }
            } else if (ui.conversations.isEmpty()) {
                item {
                    com.hashmm.app.ui.components.HmmStateView(
                        kind = com.hashmm.app.ui.components.HmmStateKind.Empty,
                        icon = Icons.AutoMirrored.Outlined.Chat,
                        title = "暂无对话",
                        message = "桌面端有正在进行的对话时，可以在这里接力继续",
                    )
                }
            }

            items(ui.conversations, key = { it.id }) { c ->
                ConvRelayCard(c, handing = ui.handingOff == c.id, hasHost = ui.hosts.isNotEmpty()) { viewModel.handoff(c) }
            }
        }
    }
}

/** 接力状态卡：SENDING 转圈 / SENT 等待接管（转圈+说明）/ ACKED 绿色已接管 / FAILED 红色失败。可点 ✕ 收起。 */
@Composable
private fun RelayStatusCard(title: String, status: RelayStatus, onDismiss: () -> Unit) {
    val (tint, label) = when (status.phase) {
        RelayPhase.SENDING -> MaterialTheme.colorScheme.primary to "正在发送…"
        RelayPhase.SENT -> MaterialTheme.colorScheme.primary to "已送达「${status.hostName}」，等待桌面端接管…"
        RelayPhase.ACKED -> Color(0xFF2E7D32) to "桌面端「${status.hostName}」已接管，可在电脑上继续"
        RelayPhase.FAILED -> MaterialTheme.colorScheme.error to "交接失败，请重试"
    }
    Surface(
        color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.fillMaxWidth().padding(start = 14.dp, top = 12.dp, bottom = 12.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            when (status.phase) {
                RelayPhase.SENDING, RelayPhase.SENT ->
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = tint)
                RelayPhase.ACKED ->
                    Icon(Icons.Outlined.CheckCircle, contentDescription = null, tint = tint, modifier = Modifier.size(20.dp))
                RelayPhase.FAILED ->
                    Icon(Icons.Outlined.ErrorOutline, contentDescription = null, tint = tint, modifier = Modifier.size(20.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface, maxLines = 1)
                Spacer(Modifier.height(2.dp))
                Text(label, fontSize = 12.sp, color = tint, lineHeight = 16.sp)
            }
            IconButton(onClick = onDismiss) {
                Icon(Icons.Outlined.Close, contentDescription = "收起", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
            }
        }
    }
}

@Composable
private fun ConvRelayCard(c: ChatConversation, handing: Boolean, hasHost: Boolean, onHandoff: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, top = 12.dp, bottom = 12.dp, end = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(c.title.ifBlank { "新对话" }, fontSize = 15.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface, maxLines = 1)
                if (c.updatedAt.isNotBlank()) Text(c.updatedAt.take(10), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.width(10.dp))
            if (handing) {
                CircularProgressIndicator(Modifier.size(20.dp).padding(end = 4.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.primary)
            } else {
                // V247：每行一个墨黑实心大胶囊太重（满屏黑块）——改安静款：浅灰容器 + 墨黑字；
                // 无在线主机时整体降为灰字（不可用一眼可判）。
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = if (hasHost) 0.62f else 0.45f),
                    shape = RoundedCornerShape(50),
                    modifier = Modifier.clip(RoundedCornerShape(50)).clickable(onClick = onHandoff),
                ) {
                    Row(Modifier.padding(horizontal = 13.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.SwapHoriz, contentDescription = null,
                            tint = if (hasHost) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(15.dp))
                        Spacer(Modifier.width(5.dp))
                        Text("转到桌面端", fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                            color = if (hasHost) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}
