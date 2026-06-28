package com.hashmm.app.ui.chat

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.unit.Dp
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.FileDownload
import androidx.compose.material.icons.outlined.FormatQuote
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material3.Icon
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import android.widget.Toast
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.jsonPrimitive
import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.ui.components.HashMascot

/** 大厂同款"正在输入"动效：三个上下/明灭跳动的圆点。比静态"…"更有生命力。 */
@Composable
fun TypingDots(color: Color, dotSize: Dp = 7.dp) {
    val tr = rememberInfiniteTransition(label = "typing")
    Row(horizontalArrangement = Arrangement.spacedBy(5.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { i ->
            val a by tr.animateFloat(
                initialValue = 0.25f, targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(durationMillis = 600, delayMillis = i * 160, easing = LinearEasing),
                    repeatMode = RepeatMode.Reverse,
                ), label = "dot$i",
            )
            Box(Modifier.size(dotSize).clip(CircleShape).background(color.copy(alpha = a)))
        }
    }
}

/** 一条消息气泡（Marvis 风格）。用户=右侧浅灰；助手=左侧带小哈头像 + Markdown。共享给首页与详情页。 */
/** 把 Supabase 的 ISO 时间(UTC)格式化成本地 HH:mm；本地新消息也走同一格式。解析失败则不显示。 */
fun formatMsgTime(createdAt: String): String {
    if (createdAt.isBlank()) return ""
    return try {
        val cleaned = createdAt.substringBefore('.').substringBefore('+').removeSuffix("Z").replace("T", " ").trim()
        val parser = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
        parser.timeZone = java.util.TimeZone.getTimeZone("UTC")
        val d = parser.parse(cleaned)
        if (d != null) java.text.SimpleDateFormat("HH:mm", java.util.Locale.US).format(d) else ""
    } catch (_: Exception) {
        Regex("T(\\d{2}:\\d{2})").find(createdAt)?.groupValues?.get(1) ?: ""
    }
}

@Composable
fun MessageBubble(msg: ChatMessage, onRegenerate: (() -> Unit)? = null, onQuote: ((String) -> Unit)? = null) {
    // 大厂同款：消息进场淡入 + 轻微上移（订正：仅 alpha+小位移，快、不打扰）。
    var shown by remember(msg.id) { mutableStateOf(false) }
    LaunchedEffect(msg.id) { shown = true }
    val a by animateFloatAsState(if (shown) 1f else 0f, tween(220), label = "msgFade")
    val ty by animateFloatAsState(if (shown) 0f else 14f, tween(220), label = "msgSlide")
    Box(Modifier.fillMaxWidth().graphicsLayer { alpha = a; translationY = ty }) {
        MessageBubbleContent(msg, onRegenerate, onQuote)
    }
}

/** 长按消息弹出的操作菜单（复制 / 引用 / 重新生成）。大厂同款上下文菜单。 */
@Composable
private fun MsgActionsMenu(
    expanded: Boolean, onDismiss: () -> Unit, content: String,
    onQuote: ((String) -> Unit)?, onRegenerate: (() -> Unit)?,
) {
    val clipboard = LocalClipboardManager.current
    val ctx = LocalContext.current
    DropdownMenu(expanded = expanded, onDismissRequest = onDismiss) {
        DropdownMenuItem(
            text = { Text("复制") },
            leadingIcon = { Icon(Icons.Outlined.ContentCopy, null, Modifier.size(18.dp)) },
            onClick = { clipboard.setText(AnnotatedString(content)); Toast.makeText(ctx, "已复制", Toast.LENGTH_SHORT).show(); onDismiss() },
        )
        if (onQuote != null) {
            DropdownMenuItem(
                text = { Text("引用") },
                leadingIcon = { Icon(Icons.Outlined.FormatQuote, null, Modifier.size(18.dp)) },
                onClick = { onQuote(content); onDismiss() },
            )
        }
        if (onRegenerate != null) {
            DropdownMenuItem(
                text = { Text("重新生成") },
                leadingIcon = { Icon(Icons.Outlined.Refresh, null, Modifier.size(18.dp)) },
                onClick = { onRegenerate(); onDismiss() },
            )
        }
    }
}

@Composable
private fun MessageBubbleContent(msg: ChatMessage, onRegenerate: (() -> Unit)? = null, onQuote: ((String) -> Unit)? = null) {
    val isUser = msg.role == "user"
    if (isUser) {
        val haptic = LocalHapticFeedback.current
        var menuOpen by remember(msg.id) { mutableStateOf(false) }
        val time = remember(msg.createdAt) { formatMsgTime(msg.createdAt) }
        Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 5.dp), horizontalAlignment = Alignment.End) {
            Box {
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp, bottomStart = 18.dp, bottomEnd = 4.dp),
                    modifier = Modifier.widthIn(max = 300.dp).pointerInput(msg.content) {
                        detectTapGestures(onLongPress = {
                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                            menuOpen = true
                        })
                    },
                ) {
                    Text(
                        msg.content,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                        color = MaterialTheme.colorScheme.onSurface,
                        fontSize = 15.sp, lineHeight = 22.sp,
                    )
                }
                MsgActionsMenu(menuOpen, { menuOpen = false }, msg.content, onQuote, null)
            }
            if (time.isNotBlank()) {
                Spacer(Modifier.height(3.dp))
                Text(time, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(end = 4.dp))
            }
        }
    } else {
        AssistantMessage(msg, onRegenerate, onQuote)
    }
}

@Composable
private fun AssistantMessage(msg: ChatMessage, onRegenerate: (() -> Unit)? = null, onQuote: ((String) -> Unit)? = null) {
    val streaming = msg.status != "complete" && msg.status != "error"
    Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 5.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = CircleShape, modifier = Modifier.size(28.dp)) {
                HashMascot(Modifier.padding(4.dp).size(20.dp))
            }
            Spacer(Modifier.width(8.dp))
            Text("HashMM", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
            if (streaming) {
                Spacer(Modifier.width(8.dp))
                TypingDots(color = MaterialTheme.colorScheme.onSurfaceVariant, dotSize = 5.dp)
            }
        }
        Spacer(Modifier.height(6.dp))
        val haptic2 = LocalHapticFeedback.current
        var menuOpen by remember(msg.id) { mutableStateOf(false) }
        Box {
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(topStart = 4.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 16.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)),
                modifier = Modifier.fillMaxWidth().padding(start = 6.dp)
                    .pointerInput(msg.content, streaming) {
                        if (!streaming) detectTapGestures(onLongPress = {
                            haptic2.performHapticFeedback(HapticFeedbackType.LongPress)
                            menuOpen = true
                        })
                    },
            ) {
                Box(Modifier.padding(horizontal = 14.dp, vertical = 11.dp)) {
                    val text = msg.content
                    when {
                        text.isBlank() && streaming -> TypingDots(color = MaterialTheme.colorScheme.onSurfaceVariant)
                        text.isBlank() -> Text("（无内容）", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        streaming -> Text(text, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface, lineHeight = 22.sp)
                        else -> MarkdownText(text, color = MaterialTheme.colorScheme.onSurface, fontSize = 15)
                    }
                }
            }
            MsgActionsMenu(menuOpen, { menuOpen = false }, msg.content, onQuote, onRegenerate)
        }
        // 文件附件卡片（桌面文件投送 / 上传的文件）——比纯文字链接好看、好点
        val files = remember(msg.files) { parseFiles(msg.files) }
        files.forEach { f -> FileAttachmentCard(f) }
        if (!streaming && msg.content.isNotBlank()) {
            val clipboard = LocalClipboardManager.current
            val haptic = LocalHapticFeedback.current
            var copied by remember(msg.id) { mutableStateOf(false) }
            LaunchedEffect(copied) { if (copied) { kotlinx.coroutines.delay(1500); copied = false } }
            Spacer(Modifier.height(4.dp))
            Row(Modifier.padding(start = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Row(
                    Modifier.clip(RoundedCornerShape(8.dp))
                        .clickable {
                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                            clipboard.setText(AnnotatedString(msg.content))
                            copied = true
                        }
                        .padding(horizontal = 8.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Outlined.ContentCopy, contentDescription = "复制",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(13.dp))
                    Spacer(Modifier.width(4.dp))
                    Text(if (copied) "已复制" else "复制", fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (onRegenerate != null) {
                    Spacer(Modifier.width(2.dp))
                    Row(
                        Modifier.clip(RoundedCornerShape(8.dp))
                            .clickable {
                                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                onRegenerate()
                            }
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "重新生成",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(13.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("重新生成", fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                val time = remember(msg.createdAt) { formatMsgTime(msg.createdAt) }
                if (time.isNotBlank()) {
                    Spacer(Modifier.width(6.dp))
                    Text(time, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        val sources = remember(msg.sources) { parseSources(msg.sources) }
        if (!streaming && sources.isNotEmpty()) SourcesSection(sources)
    }
}

/** 一条引用来源（从消息 sources JSON 解析）。 */
private data class BubbleSource(val id: Int, val filename: String, val page: Int, val snippet: String)


private fun parseSources(el: JsonElement?): List<BubbleSource> {
    val arr = el as? JsonArray ?: return emptyList()
    return arr.mapIndexedNotNull { i, item ->
        val o = item as? JsonObject ?: return@mapIndexedNotNull null
        BubbleSource(
            id = o["id"]?.jsonPrimitive?.intOrNull ?: (i + 1),
            filename = o["filename"]?.jsonPrimitive?.contentOrNull
                ?: o["modality"]?.jsonPrimitive?.contentOrNull ?: "文档",
            page = o["page"]?.jsonPrimitive?.intOrNull ?: -1,
            snippet = o["text"]?.jsonPrimitive?.contentOrNull ?: "",
        )
    }
}

// ───────── 文件附件卡片（桌面文件投送 / 上传的文件）─────────
private data class FileAttachment(val filename: String, val url: String, val size: Long)

private fun parseFiles(el: JsonElement?): List<FileAttachment> {
    val arr = el as? JsonArray ?: return emptyList()
    return arr.mapNotNull { item ->
        val o = item as? JsonObject ?: return@mapNotNull null
        val name = o["filename"]?.jsonPrimitive?.contentOrNull
            ?: o["name"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
        val url = o["url"]?.jsonPrimitive?.contentOrNull
            ?: o["download_url"]?.jsonPrimitive?.contentOrNull ?: ""
        FileAttachment(name, url, o["size"]?.jsonPrimitive?.longOrNull ?: 0L)
    }
}

private fun fileExt(name: String): String = name.substringAfterLast('.', "").lowercase()

private fun extColor(ext: String): Color = when (ext) {
    "doc", "docx" -> Color(0xFF2B6CB0)
    "xls", "xlsx", "csv" -> Color(0xFF2F855A)
    "ppt", "pptx" -> Color(0xFFDD6B20)
    "pdf" -> Color(0xFFC53030)
    "zip", "rar", "7z" -> Color(0xFF718096)
    "png", "jpg", "jpeg", "gif", "webp" -> Color(0xFF6B46C1)
    "txt", "md" -> Color(0xFF4A5568)
    else -> Color(0xFF4A5568)
}

private fun humanSize(bytes: Long): String = when {
    bytes <= 0 -> ""
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "%.0f KB".format(bytes / 1024.0)
    else -> "%.1f MB".format(bytes / (1024.0 * 1024))
}

/** 文件下载卡片：图标(扩展名)+文件名+大小，整卡可点 → 浏览器打开/下载。对齐桌面端文件卡观感。 */
@Composable
private fun FileAttachmentCard(f: FileAttachment) {
    val context = LocalContext.current
    val openFile = LocalFileOpener.current
    val ext = fileExt(f.filename)
    val sizeLabel = humanSize(f.size)
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)),
        modifier = Modifier.fillMaxWidth().padding(start = 6.dp, top = 8.dp)
            .clickable(enabled = f.url.isNotBlank()) {
                // 优先在 App 内打开（不跳浏览器）；拿不到内置打开器时退回系统打开。
                try {
                    openFile(f.filename, f.url)
                } catch (_: Exception) {
                    try {
                        context.startActivity(
                            android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(f.url))
                        )
                    } catch (_: Exception) {}
                }
            },
    ) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(42.dp).clip(RoundedCornerShape(11.dp)).background(extColor(ext)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    if (ext.isBlank()) "FILE" else ext.uppercase().take(4),
                    color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    f.filename, fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    if (sizeLabel.isBlank()) "点击查看" else "$sizeLabel · 点击查看",
                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(8.dp))
            Box(
                Modifier.size(34.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Outlined.FileDownload, contentDescription = "下载",
                    tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(18.dp),
                )
            }
        }
    }
}

/** 可折叠的「来源」区，列在助手消息下方（对齐桌面端来源卡）。 */
@Composable
private fun SourcesSection(sources: List<BubbleSource>) {
    var open by remember { mutableStateOf(false) }
    Spacer(Modifier.height(8.dp))
    Column(Modifier.padding(start = 6.dp)) {
        Row(
            Modifier.clip(RoundedCornerShape(8.dp)).clickable { open = !open }
                .padding(horizontal = 8.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Outlined.Description, contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(14.dp))
            Spacer(Modifier.width(5.dp))
            Text("${sources.size} 条来源", fontSize = 12.sp, fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(2.dp))
            Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(16.dp))
        }
        if (open) {
            Spacer(Modifier.height(2.dp))
            sources.forEach { SourceCard(it) }
        }
    }
}

@Composable
private fun SourceCard(s: BubbleSource) {
    var expanded by remember { mutableStateOf(false) }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp).clickable { expanded = !expanded },
    ) {
        Column(Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(18.dp).clip(RoundedCornerShape(5.dp))
                        .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("${s.id}", fontSize = 10.sp, fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary)
                }
                Spacer(Modifier.width(8.dp))
                Text(
                    s.filename + if (s.page > 0) "  ·  第${s.page}页" else "",
                    fontSize = 12.sp, fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                )
            }
            if (expanded && s.snippet.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(s.snippet, fontSize = 12.sp, lineHeight = 17.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
