package com.hashmm.app.ui.validity

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.EditCalendar
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Restore
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.remote.DocValidity
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import com.hashmm.app.ui.components.ScreenHeader

private val accent = com.hashmm.app.ui.theme.BrandRed   // V244：对齐品牌围巾红（旧值 0xFFEF3E36 已退役）

private data class Filter(val key: String, val label: String)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ValidityScreen(onBack: () -> Unit, viewModel: ValidityViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    var editing by remember { mutableStateOf<DocValidity?>(null) }

    LaunchedEffect(ui.msg) { ui.msg?.let { snackbar.showSnackbar(it); viewModel.clearMsg() } }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            ScreenHeader(
                title = "文档时效",
                subtitle = "生效、到期、归档与恢复",
                onBack = onBack,
                actions = {
                    IconButton(onClick = { viewModel.load() }) { Icon(Icons.Outlined.Refresh, contentDescription = "刷新") }
                    TextButton(onClick = { viewModel.sweep() }, enabled = !ui.busy) {
                        Icon(Icons.Outlined.Sync, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp)); Text("扫描过期")
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            // 状态筛选
            val filters = listOf(
                Filter("expired", "已失效"), Filter("archived", "已归档"),
                Filter("active", "生效中"), Filter("", "全部"),
            )
            Row(
                Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                // V244：M3 FilterChip（描边样式）→ 设计系统 HmmChip（选中＝墨黑实心+白字）
                filters.forEach { f ->
                    com.hashmm.app.ui.components.HmmChip(
                        text = f.label,
                        selected = ui.filter == f.key,
                        onClick = { viewModel.load(f.key) },
                    )
                }
            }

            if (ui.loading) {
                Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                    Spacer(Modifier.height(6.dp))
                    com.hashmm.app.ui.components.HmmSkeletonList(count = 5)
                }
            } else if (ui.items.isEmpty()) {
                com.hashmm.app.ui.components.HmmStateView(
                    kind = com.hashmm.app.ui.components.HmmStateKind.Empty,
                    icon = Icons.Outlined.Archive,
                    title = "这里什么都没有",
                    message = "在「知识库」给文档设置到期日，过期后会自动出现在这里。也可点右上角「扫描过期」。",
                )
            } else {
                LazyColumn(
                    Modifier.fillMaxSize().padding(horizontal = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    item { Spacer(Modifier.height(2.dp)) }
                    items(ui.items, key = { it.filename }) { d ->
                        DocValidityCard(
                            d = d,
                            busy = ui.busy,
                            onEdit = { editing = d },
                            onArchive = { viewModel.archive(d.filename) },
                            onRestore = { viewModel.restore(d.filename) },
                        )
                    }
                    item { Spacer(Modifier.height(16.dp)) }
                }
            }
        }
    }

    editing?.let { doc ->
        SetValidityDialog(
            doc = doc,
            onDismiss = { editing = null },
            onConfirm = { eff, exp, note -> editing = null; viewModel.setValidity(doc.filename, eff, exp, note) },
        )
    }
}

@Composable
private fun DocValidityCard(d: DocValidity, busy: Boolean, onEdit: () -> Unit, onArchive: () -> Unit, onRestore: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(d.filename, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f), maxLines = 2)
                Spacer(Modifier.width(8.dp))
                StatusBadge(d.status)
            }
            Spacer(Modifier.height(8.dp))
            Text(validityLine(d), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (d.note.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                Text("备注：${d.note}", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ActionChip(Icons.Outlined.EditCalendar, "设有效期", busy, onEdit)
                if (d.status == "archived") ActionChip(Icons.Outlined.Restore, "恢复", busy, onRestore)
                else ActionChip(Icons.Outlined.Archive, "归档", busy, onArchive)
            }
        }
    }
}

@Composable
private fun ActionChip(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, busy: Boolean, onClick: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.clip(RoundedCornerShape(10.dp)).clickable(enabled = !busy, onClick = onClick),
    ) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(5.dp))
            Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurface)
        }
    }
}

@Composable
private fun StatusBadge(status: String) {
    val (label, color) = when (status) {
        "expired" -> "已失效" to accent
        "archived" -> "已归档" to Color(0xFF8A8A8E)
        "pending" -> "待生效" to Color(0xFFE0A300)
        else -> "生效中" to Color(0xFF34C759)
    }
    Surface(color = color.copy(alpha = 0.15f), shape = CircleShape) {
        Row(Modifier.padding(horizontal = 10.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(color))
            Spacer(Modifier.width(5.dp))
            Text(label, fontSize = 11.sp, fontWeight = FontWeight.Medium, color = color)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SetValidityDialog(doc: DocValidity, onDismiss: () -> Unit, onConfirm: (String?, String?, String) -> Unit) {
    var eff by remember { mutableStateOf(fmtDate(doc.effectiveDate)) }
    var exp by remember { mutableStateOf(fmtDate(doc.expiryDate)) }
    var note by remember { mutableStateOf(doc.note) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("设置有效期", fontWeight = FontWeight.Bold) },
        text = {
            Column {
                Text(doc.filename, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2)
                Spacer(Modifier.height(14.dp))
                OutlinedTextField(
                    value = eff, onValueChange = { eff = it },
                    label = { Text("生效日期（YYYY-MM-DD，可空）") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = exp, onValueChange = { exp = it },
                    label = { Text("到期日期（YYYY-MM-DD，可空=不限）") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = note, onValueChange = { note = it },
                    label = { Text("备注（可空）") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(onClick = { onConfirm(eff.trim().ifBlank { null }, exp.trim().ifBlank { null }, note.trim()) }) { Text("保存") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

// ── helpers ──
private fun fmtDate(epochSec: Double?): String {
    if (epochSec == null || epochSec <= 0) return ""
    return SimpleDateFormat("yyyy-MM-dd", Locale.getDefault()).format(Date((epochSec * 1000).toLong()))
}

private fun validityLine(d: DocValidity): String {
    val eff = fmtDate(d.effectiveDate)
    val exp = fmtDate(d.expiryDate)
    val effPart = if (eff.isNotBlank()) "生效 $eff" else "生效 —"
    val expPart = if (exp.isNotBlank()) "到期 $exp" else "到期 不限"
    val days = d.expiryDate?.let { ((it - System.currentTimeMillis() / 1000.0) / 86400.0) }
    val daysPart = when {
        days == null -> ""
        days < 0 -> " · 已过期 ${(-days).toInt()} 天"
        else -> " · 剩 ${days.toInt()} 天"
    }
    return "$effPart · $expPart$daysPart"
}
