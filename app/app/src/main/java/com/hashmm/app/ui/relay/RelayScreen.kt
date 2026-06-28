package com.hashmm.app.ui.relay
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.DesktopWindows
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
                Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.Top) {
                        Icon(Icons.Outlined.SwapHoriz, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp))
                        Spacer(Modifier.width(12.dp))
                        Column {
                            Text("设备间接力", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "对话在后端持续运行，关掉 app 也不会中断。需要合盖时把它交给桌面端继续，稍后再切回手机。",
                                fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 19.sp,
                            )
                        }
                    }
                }
            }

            // 在线桌面主机
            item {
                Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(40.dp).clip(RoundedCornerShape(11.dp)).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.10f)), contentAlignment = Alignment.Center) {
                            Icon(Icons.Outlined.DesktopWindows, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
                        }
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

            item {
                Text("选择对话交接", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp, start = 2.dp))
            }

            if (ui.loading && ui.conversations.isEmpty()) {
                item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
            } else if (ui.conversations.isEmpty()) {
                item {
                    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                            Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(32.dp))
                            Spacer(Modifier.height(8.dp))
                            Text("暂无对话", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }

            items(ui.conversations, key = { it.id }) { c ->
                ConvRelayCard(c, handing = ui.handingOff == c.id, hasHost = ui.hosts.isNotEmpty()) { viewModel.handoff(c) }
            }
        }
    }
}

@Composable
private fun ConvRelayCard(c: ChatConversation, handing: Boolean, hasHost: Boolean, onHandoff: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, top = 12.dp, bottom = 12.dp, end = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(c.title.ifBlank { "新对话" }, fontSize = 15.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface, maxLines = 1)
                if (c.updatedAt.isNotBlank()) Text(c.updatedAt.take(10), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.width(10.dp))
            if (handing) {
                CircularProgressIndicator(Modifier.size(20.dp).padding(end = 4.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.primary)
            } else {
                Surface(
                    color = if (hasHost) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.clip(RoundedCornerShape(10.dp)).clickable(onClick = onHandoff),
                ) {
                    Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.SwapHoriz, contentDescription = null, tint = if (hasHost) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(5.dp))
                        Text("转到桌面端", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = if (hasHost) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}
