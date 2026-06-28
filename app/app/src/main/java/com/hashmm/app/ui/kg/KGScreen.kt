package com.hashmm.app.ui.kg
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlin.math.hypot
import kotlin.math.min

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun KGScreen(onBack: () -> Unit, viewModel: KGViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var scale by remember { mutableStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var selected by remember { mutableStateOf<String?>(null) }
    var query by remember { mutableStateOf("") }

    Scaffold(
        topBar = {
            ScreenHeader(title = "知识图谱", onBack = onBack) {
                IconButton(onClick = { scale = 1f; offset = Offset.Zero; selected = null; query = ""; viewModel.load() }) { Icon(Icons.Outlined.Refresh, contentDescription = "刷新") }
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            // stats
            val g = ui.graph
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                StatChip("实体", g.entities, Modifier.weight(1f))
                StatChip("关系", g.relations, Modifier.weight(1f))
                StatChip("社区", g.communities, Modifier.weight(1f))
            }

            // 选中节点的邻居集合（高亮用）
            val neighbors = remember(selected, g.edges) {
                val id = selected
                if (id == null) emptySet() else buildSet {
                    g.edges.forEach { e -> if (e.from == id) add(e.to); if (e.to == id) add(e.from) }
                }
            }
            // 搜索匹配
            val matches = remember(query, g.nodes) {
                if (query.isBlank()) emptyList()
                else g.nodes.filter { it.label.contains(query, ignoreCase = true) }.take(8)
            }
            if (g.nodes.isNotEmpty()) {
                OutlinedTextField(
                    value = query,
                    onValueChange = { query = it },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                    placeholder = { Text("搜索实体…") },
                    leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
                    singleLine = true,
                )
                if (matches.isNotEmpty()) {
                    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(12.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)) {
                        Column {
                            matches.forEach { node ->
                                Text(
                                    node.label,
                                    fontSize = 14.sp,
                                    color = MaterialTheme.colorScheme.onSurface,
                                    modifier = Modifier.fillMaxWidth()
                                        .clickable { selected = node.id; scale = 1f; offset = Offset.Zero; query = "" }
                                        .padding(horizontal = 14.dp, vertical = 10.dp),
                                )
                            }
                        }
                    }
                }
            }

            Box(Modifier.fillMaxSize().padding(horizontal = 12.dp).clip(RoundedCornerShape(16.dp)).background(MaterialTheme.colorScheme.surface)) {
                when {
                    ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    g.nodes.isEmpty() -> Column(Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Outlined.Hub, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(40.dp))
                        Spacer(Modifier.height(10.dp))
                        Text(g.error ?: "暂无图谱", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    else -> {
                        val nodeColor = MaterialTheme.colorScheme.onSurface
                        val labelColor = MaterialTheme.colorScheme.onSurface
                        val edgeColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.35f)
                        Canvas(
                            Modifier.fillMaxSize()
                                .pointerInput(Unit) {
                                    detectTapGestures { tap ->
                                        val w = size.width.toFloat(); val h = size.height.toFloat()
                                        val base = min(w, h) / 2f * 0.82f
                                        val center = Offset(w / 2f, h / 2f)
                                        var best: String? = null; var bestD = Float.MAX_VALUE
                                        ui.positions.forEach { (id, p) ->
                                            val sp = center + offset + Offset(p.x * base * scale, p.y * base * scale)
                                            val d = hypot(sp.x - tap.x, sp.y - tap.y)
                                            if (d < bestD) { bestD = d; best = id }
                                        }
                                        selected = if (bestD < 60f) best else null
                                    }
                                }
                                .pointerInput(Unit) {
                                    detectTransformGestures { _, pan, zoom, _ ->
                                        scale = (scale * zoom).coerceIn(0.4f, 6f)
                                        offset += pan
                                    }
                                },
                        ) {
                            val w = size.width; val h = size.height
                            val base = min(w, h) / 2f * 0.82f
                            val center = Offset(w / 2f, h / 2f)
                            fun sp(id: String): Offset? {
                                val p = ui.positions[id] ?: return null
                                return center + offset + Offset(p.x * base * scale, p.y * base * scale)
                            }
                            // 边（选中时仅高亮相连边，其余淡化）
                            g.edges.forEach { e ->
                                val a = sp(e.from); val b = sp(e.to)
                                if (a != null && b != null) {
                                    val touches = selected != null && (e.from == selected || e.to == selected)
                                    val col = when {
                                        selected == null -> edgeColor
                                        touches -> labelColor.copy(alpha = 0.5f)
                                        else -> edgeColor.copy(alpha = 0.12f)
                                    }
                                    drawLine(col, a, b, strokeWidth = if (touches) 2.5f else 1.5f)
                                }
                            }
                            // 节点（选中时非邻居淡化）
                            g.nodes.forEach { node ->
                                val c = sp(node.id) ?: return@forEach
                                val isSel = node.id == selected
                                val isNbr = node.id in neighbors
                                val dim = selected != null && !isSel && !isNbr
                                val baseCol = runCatching { Color(android.graphics.Color.parseColor(node.color)) }.getOrDefault(nodeColor)
                                val col = if (dim) baseCol.copy(alpha = 0.18f) else baseCol
                                val r = (node.size.coerceIn(8, 40) / 2f) * scale.coerceIn(0.6f, 2.2f)
                                if (isSel) drawCircle(labelColor, r + 4f, c)
                                drawCircle(col, r, c)
                            }
                            // 标签：选中态显示「选中+邻居」，否则显示大节点
                            drawIntoCanvas { canvas ->
                                val paint = android.graphics.Paint().apply {
                                    isAntiAlias = true
                                    textAlign = android.graphics.Paint.Align.CENTER
                                    color = android.graphics.Color.argb(255, (labelColor.red * 255).toInt(), (labelColor.green * 255).toInt(), (labelColor.blue * 255).toInt())
                                }
                                g.nodes.forEach { node ->
                                    val isSel = node.id == selected
                                    val show = isSel || node.id in neighbors || (selected == null && node.size >= 22)
                                    if (!show) return@forEach
                                    val c = sp(node.id) ?: return@forEach
                                    paint.textSize = if (isSel) 34f else 24f
                                    paint.isFakeBoldText = isSel
                                    val r = (node.size.coerceIn(8, 40) / 2f) * scale.coerceIn(0.6f, 2.2f)
                                    canvas.nativeCanvas.drawText(node.label, c.x, c.y - r - 6f, paint)
                                }
                            }
                        }
                        // 选中详情
                        selected?.let { id ->
                            val node = g.nodes.firstOrNull { it.id == id }
                            if (node != null) {
                                Surface(
                                    color = MaterialTheme.colorScheme.background.copy(alpha = 0.95f),
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.align(Alignment.BottomStart).padding(12.dp).clickable { selected = null },
                                ) {
                                    Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                                        Text(node.label, fontSize = 15.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
                                        Text("类型：${node.type} · ${neighbors.size} 个关联", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        Text("点此取消高亮", fontSize = 11.sp, color = MaterialTheme.colorScheme.primary)
                                    }
                                }
                            }
                        }
                        // 提示
                        Text(
                            if (selected == null) "双指缩放 · 拖动 · 点节点或搜索" else "已高亮关联实体",
                            fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.align(Alignment.TopEnd).padding(10.dp),
                        )
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun StatChip(label: String, value: Int, modifier: Modifier = Modifier) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(12.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = modifier) {
        Column(Modifier.padding(vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text("$value", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
            Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
