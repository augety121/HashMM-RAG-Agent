package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import com.hashmm.app.ui.components.HashMascot
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AdminPanelSettings
import androidx.compose.material.icons.outlined.BarChart
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.DesktopWindows
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.MenuBook
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.SwapHoriz
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * 原生工作台：不再用 WebView，而是把客户端能力做成原生入口聚合在一起。
 * 已原生：知识库、记忆中心、远程控制；其余（模型配置、文档上传…）逐步补。
 * （原「网页版完整客户端」兜底卡片已按用户要求移除。）
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkbenchHubScreen(
    onOpenKnowledge: () -> Unit,
    onOpenMemory: () -> Unit,
    onOpenRemote: () -> Unit,
    onOpenWebClient: () -> Unit,
    onOpenModels: () -> Unit = {},
    onOpenKg: () -> Unit = {},
    onOpenAdmin: () -> Unit = {},
    onOpenRelay: () -> Unit = {},
    onOpenUsage: () -> Unit = {},
    onOpenValidity: () -> Unit = {},
    viewModel: WorkbenchHubViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
    ) {
        Spacer(Modifier.height(28.dp))
        // Hero：大号小哈 + 名称 + 状态（点状态可刷新）
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
            HashMascot(Modifier.size(92.dp))
            Spacer(Modifier.height(12.dp))
            Text("HashMM", fontSize = 25.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(2.dp))
            Text("电脑端客户端 · 原生入口", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(14.dp))
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = CircleShape,
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
                modifier = Modifier.clip(CircleShape).clickable { viewModel.refresh() },
            ) {
                Row(Modifier.padding(horizontal = 16.dp, vertical = 9.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (ui.checking) {
                        CircularProgressIndicator(Modifier.size(12.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    } else {
                        Box(Modifier.size(9.dp).clip(CircleShape).background(if (ui.online) Color(0xFF34C759) else Color(0xFFBBBBBB)))
                    }
                    Spacer(Modifier.width(8.dp))
                    Text(if (ui.online) "客户端在线" else "客户端离线", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                    if (ui.clientUrl.isNotBlank()) {
                        Spacer(Modifier.width(8.dp))
                        Text(ui.clientUrl, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                    }
                }
            }
        }
        Spacer(Modifier.height(26.dp))

        // 接力（设备交接）—— 醒目整条
        Surface(
            color = MaterialTheme.colorScheme.primary,
            shape = RoundedCornerShape(18.dp),
            modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).clickable(onClick = onOpenRelay),
        ) {
            Row(Modifier.fillMaxWidth().padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.SwapHoriz, contentDescription = null, tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.size(26.dp))
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text("接力", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onPrimary)
                    Text("把对话交给桌面端继续 · 合盖也不中断", fontSize = 12.sp, color = MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.8f))
                }
                Icon(Icons.Outlined.ChevronRight, contentDescription = null, tint = MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.9f))
            }
        }
        Spacer(Modifier.height(18.dp))

        Text("客户端能力", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.padding(start = 2.dp, bottom = 10.dp))

        // 两列功能格
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            FeatureCard(Icons.Outlined.MenuBook, "知识库", "语料 · 文档", Modifier.weight(1f), onOpenKnowledge)
            FeatureCard(Icons.Outlined.Psychology, "记忆中心", "跨会话偏好", Modifier.weight(1f), onOpenMemory)
        }
        Spacer(Modifier.height(12.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            FeatureCard(Icons.Outlined.DesktopWindows, "远程控制", "投屏 · 操控", Modifier.weight(1f), onOpenRemote)
            FeatureCard(Icons.Outlined.Tune, "模型 / 后端", "选模型 · 切后端", Modifier.weight(1f), onOpenModels)
        }
        Spacer(Modifier.height(12.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            FeatureCard(Icons.Outlined.Hub, "知识图谱", "实体 · 关系", Modifier.weight(1f), onOpenKg)
            FeatureCard(Icons.Outlined.AdminPanelSettings, "管理后台", "用户 · 审计", Modifier.weight(1f), onOpenAdmin)
        }
        Spacer(Modifier.height(12.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            FeatureCard(Icons.Outlined.BarChart, "用量", "Token · 成本", Modifier.weight(1f), onOpenUsage)
            FeatureCard(Icons.Outlined.Schedule, "失效区", "时效 · 归档", Modifier.weight(1f), onOpenValidity)
        }
        Spacer(Modifier.height(24.dp))
        // 「网页版完整客户端」卡片已按用户要求删除（点开没内容、用不上）。
    }
}

@Composable
private fun FeatureCard(
    icon: ImageVector, title: String, subtitle: String, modifier: Modifier = Modifier,
    onClick: () -> Unit, soon: Boolean = false,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(18.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
        modifier = modifier.height(126.dp).clickable(enabled = !soon, onClick = onClick),
    ) {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            Box(
                Modifier.size(48.dp).clip(RoundedCornerShape(14.dp))
                    .background(if (soon) MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, contentDescription = title, tint = if (soon) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.primary, modifier = Modifier.size(24.dp))
            }
            Spacer(Modifier.weight(1f))
            Text(title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(3.dp))
            Text(subtitle, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
