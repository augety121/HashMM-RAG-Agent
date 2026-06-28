package com.hashmm.app.ui.admin
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.AdminPanelSettings
import androidx.compose.material.icons.outlined.Refresh
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
import com.hashmm.app.data.remote.AdminUser
import com.hashmm.app.data.remote.AuditEntry
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AdminScreen(onBack: () -> Unit, viewModel: AdminViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    Scaffold(
        topBar = {
            ScreenHeader(title = "管理后台", onBack = onBack) {
                IconButton(onClick = { viewModel.refresh() }) { Icon(Icons.Outlined.Refresh, contentDescription = "刷新") }
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            TabRow(selectedTabIndex = if (ui.tab == AdminTab.USERS) 0 else 1, containerColor = MaterialTheme.colorScheme.background) {
                Tab(selected = ui.tab == AdminTab.USERS, onClick = { viewModel.selectTab(AdminTab.USERS) }, text = { Text("用户") })
                Tab(selected = ui.tab == AdminTab.AUDIT, onClick = { viewModel.selectTab(AdminTab.AUDIT) }, text = { Text("审计") })
            }
            Box(Modifier.fillMaxSize()) {
                when {
                    ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    ui.tab == AdminTab.USERS -> UsersList(ui.users, ui.usersError)
                    else -> AuditList(ui.audit, ui.auditError)
                }
            }
        }
    }
}

@Composable
private fun UsersList(users: List<AdminUser>, error: String?) {
    if (users.isEmpty()) { EmptyState(error ?: "暂无用户"); return }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Text("用户 ${users.size}", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        items(users, key = { it.id }) { u -> UserCard(u) }
    }
}

@Composable
private fun AuditList(entries: List<AuditEntry>, error: String?) {
    if (entries.isEmpty()) { EmptyState(error ?: "暂无审计记录"); return }
    val fmt = remember { SimpleDateFormat("MM-dd HH:mm:ss", Locale.getDefault()) }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item { Text("最近 ${entries.size} 条工具调用", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        items(entries.size) { i -> AuditCard(entries[i], fmt) }
    }
}

@Composable
private fun EmptyState(msg: String) {
    Column(Modifier.fillMaxSize().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Icon(Icons.Outlined.AdminPanelSettings, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(40.dp))
        Spacer(Modifier.height(10.dp))
        Text(msg, fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun UserCard(u: AdminUser) {
    val isAdmin = u.role.equals("admin", ignoreCase = true)
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)), contentAlignment = Alignment.Center) {
                Text(u.username.take(1).uppercase(), fontSize = 16.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(u.username, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                if (u.createdAt.isNotBlank()) Text("注册 ${u.createdAt.take(10)}", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TagBadge(if (isAdmin) "管理员" else "用户", isAdmin)
        }
    }
}

@Composable
private fun AuditCard(e: AuditEntry, fmt: SimpleDateFormat) {
    val high = e.risk.equals("high", ignoreCase = true)
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(12.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(if (e.ok) Color(0xFF34C759) else MaterialTheme.colorScheme.error))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(e.tool, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                val time = if (e.ts > 0) fmt.format(Date((e.ts * 1000).toLong())) else ""
                Text(listOf(e.actor, time, "${e.latencyMs}ms").filter { it.isNotBlank() }.joinToString(" · "), fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (high) TagBadge("高风险", false, danger = true)
        }
    }
}

@Composable
private fun TagBadge(text: String, admin: Boolean, danger: Boolean = false) {
    val bg = when { danger -> MaterialTheme.colorScheme.error.copy(alpha = 0.14f); admin -> Color(0xFF34C759).copy(alpha = 0.15f); else -> MaterialTheme.colorScheme.surfaceVariant }
    val fg = when { danger -> MaterialTheme.colorScheme.error; admin -> Color(0xFF1E8E3E); else -> MaterialTheme.colorScheme.onSurfaceVariant }
    Surface(color = bg, shape = RoundedCornerShape(8.dp)) {
        Text(text, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = fg, modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
    }
}
