package com.hashmm.app.ui.admin

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.remote.AdminUser
import com.hashmm.app.data.remote.AuditEntry
import com.hashmm.app.ui.components.HmmBadge
import com.hashmm.app.ui.components.HmmCard
import com.hashmm.app.ui.components.HmmChip
import com.hashmm.app.ui.components.HmmDivider
import com.hashmm.app.ui.components.HmmPageScaffold
import com.hashmm.app.ui.components.HmmSectionTitle
import com.hashmm.app.ui.components.HmmSkeletonCard
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import com.hashmm.app.ui.components.HmmStatCard
import com.hashmm.app.ui.components.HmmTone
import com.hashmm.app.ui.theme.AppSpacing
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun AdminScreen(onBack: () -> Unit, viewModel: AdminViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    HmmPageScaffold(
        title = "团队管理",
        subtitle = "成员、角色和安全记录",
        onBack = onBack,
        actions = {
            IconButton(onClick = viewModel::refresh) { Icon(Icons.Outlined.Refresh, contentDescription = "刷新") }
        },
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(horizontal = AppSpacing.page, vertical = AppSpacing.md),
            verticalArrangement = Arrangement.spacedBy(AppSpacing.md),
        ) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(AppSpacing.sm)) {
                    HmmChip("成员", { viewModel.selectTab(AdminTab.USERS) }, ui.tab == AdminTab.USERS)
                    HmmChip("安全记录", { viewModel.selectTab(AdminTab.AUDIT) }, ui.tab == AdminTab.AUDIT)
                }
            }
            if (ui.loading) {
                items(3) { HmmSkeletonCard(lines = 2) }
            } else if (ui.tab == AdminTab.USERS) {
                usersContent(ui.users, ui.usersNotice, ui.usersError)
            } else {
                auditContent(ui.audit, ui.auditError)
            }
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.usersContent(
    users: List<AdminUser>,
    notice: String?,
    error: String?,
) {
    if (users.isEmpty()) {
        item {
            HmmStateView(HmmStateKind.Error, title = "暂时无法读取成员", message = error ?: "团队里还没有其他成员", icon = Icons.Outlined.Groups)
        }
        return
    }
    if (!notice.isNullOrBlank()) {
        item {
            HmmCard {
                Row(verticalAlignment = Alignment.Top) {
                    Icon(
                        Icons.Outlined.Info,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.tertiary,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(AppSpacing.sm))
                    Column {
                        Text("成员目录已部分同步", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                        Spacer(Modifier.height(AppSpacing.xs))
                        Text(notice, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
    val adminCount = users.count { it.role.equals("admin", true) }
    val cloudCount = users.count { it.identitySource == "supabase" }
    item {
        HmmCard {
            Text("团队一览", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(AppSpacing.md))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(AppSpacing.md)) {
                HmmStatCard("全部成员", users.size.toString(), Modifier.weight(1f), "人", Icons.Outlined.Groups, HmmTone.Accent)
                HmmStatCard("管理员", adminCount.toString(), Modifier.weight(1f), "人", Icons.Outlined.Security)
            }
            Spacer(Modifier.height(AppSpacing.sm))
            Text("$cloudCount 个账号已与桌面端同步", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    item { HmmSectionTitle("成员") }
    item {
        HmmCard(padding = false) {
            users.forEachIndexed { index, user ->
                if (index > 0) HmmDivider(Modifier.padding(start = 66.dp))
                UserRow(user)
            }
        }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.auditContent(entries: List<AuditEntry>, error: String?) {
    if (entries.isEmpty()) {
        item {
            HmmStateView(HmmStateKind.Empty, title = "还没有安全记录", message = error ?: "需要确认的操作会显示在这里", icon = Icons.Outlined.Security)
        }
        return
    }
    item {
        HmmCard {
            Text("最近的安全活动", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(AppSpacing.xs))
            Text("帮助你确认谁在什么时候使用了哪些能力", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    item { HmmSectionTitle("最近 ${entries.size} 条") }
    item {
        val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
        HmmCard(padding = false) {
            entries.forEachIndexed { index, entry ->
                if (index > 0) HmmDivider(Modifier.padding(start = 34.dp))
                AuditRow(entry, fmt)
            }
        }
    }
}

@Composable
private fun UserRow(user: AdminUser) {
    val isAdmin = user.role.equals("admin", true)
    Row(Modifier.fillMaxWidth().padding(AppSpacing.lg), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(42.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
            Text((user.displayName.ifBlank { user.username }).take(1).uppercase(), fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.width(AppSpacing.md))
        Column(Modifier.weight(1f)) {
            Text(user.displayName.ifBlank { user.username }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
            val detail = user.email.ifBlank { user.username }
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (user.lastSignInAt.isNotBlank()) Text("最近登录 ${user.lastSignInAt.take(10)}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        HmmBadge(if (isAdmin) "管理员" else "成员", if (isAdmin) HmmTone.Success else HmmTone.Default)
    }
}

@Composable
private fun AuditRow(entry: AuditEntry, fmt: SimpleDateFormat) {
    Row(Modifier.fillMaxWidth().padding(AppSpacing.lg), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(if (entry.ok) Color(0xFF22A06B) else MaterialTheme.colorScheme.error))
        Spacer(Modifier.width(AppSpacing.md))
        Column(Modifier.weight(1f)) {
            Text(friendlyToolName(entry.tool), style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
            val time = if (entry.ts > 0) fmt.format(Date((entry.ts * 1000).toLong())) else ""
            Text(listOf(entry.actor, time).filter { it.isNotBlank() }.joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (entry.risk.equals("high", true)) HmmBadge("需关注", HmmTone.Warning)
    }
}

private fun friendlyToolName(tool: String): String = when {
    tool.startsWith("browser") -> "使用浏览器完成任务"
    tool.startsWith("computer") -> "操作已连接的电脑"
    tool.contains("file") -> "读取或整理文件"
    tool.contains("search") -> "检索资料"
    else -> tool.replace('_', ' ')
}
