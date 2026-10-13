package com.hashmm.app.ui.kg
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.CenterFocusStrong
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
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
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
            ScreenHeader(title = "知识图谱", subtitle = "实体 · 关系 · 社区 · 图谱检索", onBack = onBack) {
                IconButton(onClick = { scale = 1f; offset = Offset.Zero; selected = null; query = ""; viewModel.load() }) { Icon(Icons.Outlined.Refresh, contentDescription = "刷新") }
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            // stats
            val g = ui.graph
            // V244：三个描边小卡合并为一张三格概览白卡（与工作台子页 StatTriple 同语言）
            Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                Row(Modifier.fillMaxWidth().padding(vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                    val cells = listOf("实体" to g.entities, "关系" to g.relations, "社区" to g.communities)
                    cells.forEachIndexed { i, (label, v) ->
                        Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("$v", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold,
                                color = MaterialTheme.colorScheme.onSurface, letterSpacing = (-0.5).sp)
                            Spacer(Modifier.height(2.dp))
                            Text(label, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (i < cells.size - 1) Box(Modifier.width(1.dp).height(26.dp)
                            .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)))
                    }
                }
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
                // V245：M3 描边输入框 → 白色圆角搜索条（与全 App 输入语言一致）
                Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 13.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Search, contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(19.dp))
                        Spacer(Modifier.width(10.dp))
                        Box(Modifier.weight(1f)) {
                            if (query.isEmpty()) Text("搜索实体…", fontSize = 14.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.8f))
                            androidx.compose.foundation.text.BasicTextField(
                                value = query, onValueChange = { query = it }, singleLine = true,
                                textStyle = androidx.compose.ui.text.TextStyle(fontSize = 14.sp,
                                    color = MaterialTheme.colorScheme.onSurface),
                                cursorBrush = androidx.compose.ui.graphics.SolidColor(MaterialTheme.colorScheme.onSurface),
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                        if (query.isNotEmpty()) Text("清除", fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.clickable { query = "" })
                    }
                }
                if (matches.isNotEmpty()) {
                    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)) {
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

            Box(Modifier.fillMaxSize().padding(horizontal = 16.dp).clip(RoundedCornerShape(16.dp)).background(MaterialTheme.colorScheme.surface)) {
                when {
                    ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    g.error != null && g.error != "图谱为空（尚未构建实体）" -> androidx.compose.foundation.layout.Box(Modifier.align(Alignment.Center)) {
                        com.hashmm.app.ui.components.HmmStateView(
                            kind = com.hashmm.app.ui.components.HmmStateKind.Error,
                            icon = Icons.Outlined.Hub,
                            title = "暂时无法读取图谱",
                            message = g.error,
                            onRetry = viewModel::load,
                        )
                    }
                    g.nodes.isEmpty() -> androidx.compose.foundation.layout.Box(Modifier.align(Alignment.Center)) {
                        com.hashmm.app.ui.components.HmmStateView(
                            kind = com.hashmm.app.ui.components.HmmStateKind.Empty,
                            icon = Icons.Outlined.Hub,
                            title = "还没有知识图谱",
                            message = "先在知识库导入资料并完成索引，实体、关系和社区会显示在这里。",
                        )
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
                            // 节点（V245：后端随机色 → 品牌灰阶按度数分层——大节点墨黑、中间灰、叶子浅灰；
                            //        选中节点用围巾红点睛 + 墨黑外环；选中时非邻居淡化）
                            g.nodes.forEach { node ->
                                val c = sp(node.id) ?: return@forEach
                                val isSel = node.id == selected
                                val isNbr = node.id in neighbors
                                val dim = selected != null && !isSel && !isNbr
                                val baseCol = when {
                                    node.size >= 22 -> nodeColor                     // 枢纽：墨黑
                                    node.size >= 14 -> Color(0xFF77777D)             // 中层：中灰
                                    else -> Color(0xFFC4C4C9)                        // 叶子：浅灰
                                }
                                val col = when {
                                    isSel -> com.hashmm.app.ui.theme.BrandRed
                                    dim -> baseCol.copy(alpha = 0.16f)
                                    else -> baseCol
                                }
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
                                // V245：标签加白色描边打底——文字压在点/线上也不糊
                                val halo = android.graphics.Paint().apply {
                                    isAntiAlias = true
                                    textAlign = android.graphics.Paint.Align.CENTER
                                    style = android.graphics.Paint.Style.STROKE
                                    strokeWidth = 7f
                                    color = android.graphics.Color.WHITE
                                }
                                g.nodes.forEach { node ->
                                    val isSel = node.id == selected
                                    val show = isSel || node.id in neighbors || (selected == null && node.size >= 22)
                                    if (!show) return@forEach
                                    val c = sp(node.id) ?: return@forEach
                                    paint.textSize = if (isSel) 34f else 24f
                                    paint.isFakeBoldText = isSel
                                    halo.textSize = paint.textSize
                                    halo.isFakeBoldText = isSel
                                    val r = (node.size.coerceIn(8, 40) / 2f) * scale.coerceIn(0.6f, 2.2f)
                                    canvas.nativeCanvas.drawText(node.label, c.x, c.y - r - 6f, halo)
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
                        // 提示（V245：白胶囊，不再裸字压在图上）
                        Surface(
                            color = MaterialTheme.colorScheme.background.copy(alpha = 0.92f),
                            shape = RoundedCornerShape(50),
                            modifier = Modifier.align(Alignment.TopEnd).padding(10.dp),
                        ) {
                            Text(
                                if (selected == null) "双指缩放 · 拖动 · 点节点或搜索" else "已高亮关联实体",
                                fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                            )
                        }
                        // V245：视图复位钮（缩放/平移跑远后一键回中）
                        Surface(
                            color = MaterialTheme.colorScheme.surface,
                            shape = CircleShape,
                            shadowElevation = 4.dp,
                            modifier = Modifier.align(Alignment.BottomEnd).padding(14.dp)
                                .clip(CircleShape)
                                .clickable { scale = 1f; offset = Offset.Zero; selected = null },
                        ) {
                            Box(Modifier.size(40.dp), contentAlignment = Alignment.Center) {
                                Icon(Icons.Outlined.CenterFocusStrong, contentDescription = "复位视图",
                                    tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(20.dp))
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}
