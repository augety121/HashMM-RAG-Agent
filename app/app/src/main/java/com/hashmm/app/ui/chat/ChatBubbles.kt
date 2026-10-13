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
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.TextButton
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.foundation.border
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.FileDownload
import androidx.compose.material.icons.outlined.FormatQuote
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.ThumbUp
import androidx.compose.material.icons.outlined.ThumbDown
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
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.jsonPrimitive
import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.ui.components.HashMascot

private fun inputOptions(value: JsonElement?): List<String> =
    (value as? JsonArray)
        ?.mapNotNull { runCatching { it.jsonPrimitive.contentOrNull?.trim() }.getOrNull() }
        ?.filter { it.isNotBlank() }
        ?.distinct()
        ?.take(3)
        .orEmpty()

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
    // Detail bubbles show the same unambiguous timestamp as the message
    // details view. Do not reduce a cross-day message to a misleading HH:mm.
    return formatChatTimeFull(createdAt)
}

@Composable
fun MessageBubble(msg: ChatMessage, onRegenerate: (() -> Unit)? = null, onQuote: ((String) -> Unit)? = null, onConfirmPlan: ((String, String) -> Unit)? = null, onMemAction: ((String, String) -> Unit)? = null, onCancelTask: ((String) -> Unit)? = null, onInputAnswer: ((String) -> Unit)? = null, onToolApproval: ((String, Boolean) -> Unit)? = null, onFeedback: ((String, String, String, String) -> Unit)? = null, awaiting: Boolean = false, animateIn: Boolean = true) {
    // V272 卡顿优化：进场动画只给**新消息**。打开会话时历史消息是一整批，若每条都淡入+上移会造成
    // 首屏批量动画卡顿、点进去有明显停顿。这里在首次组合时捕获 animateIn（之后不变，避免结构切换/
    // 重复动画）：历史批量渲染 doAnim=false → 初始即 shown、tween 0ms → 秒显不卡；新追加的消息
    // doAnim=true → 正常淡入。让"点进去"瞬间就有内容、丝滑。
    val doAnim = remember(msg.id) { animateIn }
    var shown by remember(msg.id) { mutableStateOf(!doAnim) }
    LaunchedEffect(msg.id) { if (doAnim) shown = true }
    val a by animateFloatAsState(if (shown) 1f else 0f, tween(if (doAnim) 220 else 0), label = "msgFade")
    val ty by animateFloatAsState(if (shown) 0f else 14f, tween(if (doAnim) 220 else 0), label = "msgSlide")
    Box(Modifier.fillMaxWidth().graphicsLayer { alpha = a; translationY = ty }) {
        MessageBubbleContent(msg, onRegenerate, onQuote, onConfirmPlan, onMemAction, onCancelTask, onInputAnswer, onToolApproval, onFeedback, awaiting)
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
        DropdownMenuItem(
            text = { Text("分享") },
            leadingIcon = { Icon(Icons.Outlined.Share, null, Modifier.size(18.dp)) },
            onClick = {
                try {
                    val send = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
                        type = "text/plain"; putExtra(android.content.Intent.EXTRA_TEXT, content)
                    }
                    ctx.startActivity(android.content.Intent.createChooser(send, "分享到"))
                } catch (_: Exception) {}
                onDismiss()
            },
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
private fun MessageBubbleContent(msg: ChatMessage, onRegenerate: (() -> Unit)? = null, onQuote: ((String) -> Unit)? = null, onConfirmPlan: ((String, String) -> Unit)? = null, onMemAction: ((String, String) -> Unit)? = null, onCancelTask: ((String) -> Unit)? = null, onInputAnswer: ((String) -> Unit)? = null, onToolApproval: ((String, Boolean) -> Unit)? = null, onFeedback: ((String, String, String, String) -> Unit)? = null, awaiting: Boolean = false) {
    val isUser = msg.role == "user"
    if (isUser) {
        val haptic = LocalHapticFeedback.current
        var menuOpen by remember(msg.id) { mutableStateOf(false) }
        val time = remember(msg.createdAt) { formatMsgTime(msg.createdAt) }
        Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 5.dp), horizontalAlignment = Alignment.End) {
            Box {
                Surface(
                    // V250 方案定死：用户气泡＝暖色 #F6ECE7，圆角 16/16/4/16（右下收角，指向发送者）
                    color = com.hashmm.app.ui.theme.WarmBeige,
                    shape = RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = 4.dp, bottomEnd = 16.dp),
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
        AssistantMessage(msg, onRegenerate, onQuote, onConfirmPlan, onMemAction, onCancelTask, onInputAnswer, onToolApproval, onFeedback, awaiting)
    }
}

@Composable
private fun AssistantMessage(msg: ChatMessage, onRegenerate: (() -> Unit)? = null, onQuote: ((String) -> Unit)? = null, onConfirmPlan: ((String, String) -> Unit)? = null, onMemAction: ((String, String) -> Unit)? = null, onCancelTask: ((String) -> Unit)? = null, onInputAnswer: ((String) -> Unit)? = null, onToolApproval: ((String, Boolean) -> Unit)? = null, onFeedback: ((String, String, String, String) -> Unit)? = null, awaiting: Boolean = false) {
    val streaming = msg.status == "streaming"
    val waitingForInput = msg.status == "waiting_input"
    val waitingForApproval = msg.status == "waiting_approval"
    val toolApproval = remember(msg.runManifest) { parseToolApproval(msg.runManifest) }
    val inputResolved = msg.status == "resolved" && toolApproval == null
    // plan 模式：消息里带 ⟦CONFIRM|kind|payload⟧ → 抽出 (kind,payload)、隐藏标记、底部显示确认/取消按钮
    val planMatch = remember(msg.content) { Regex("⟦CONFIRM\\|([a-z_]+)\\|([\\s\\S]+?)⟧").find(msg.content) }
    val planKind = planMatch?.groupValues?.getOrNull(1)?.trim()
    val planPayload = planMatch?.groupValues?.getOrNull(2)?.trim()
    val tasksJson = remember(msg.content) { Regex("⟦TASKS:([\\s\\S]+?)⟧").find(msg.content)?.groupValues?.getOrNull(1) }
    val memJson = remember(msg.content) { Regex("⟦MEM:([\\s\\S]+?)⟧").find(msg.content)?.groupValues?.getOrNull(1) }
    // V202 澄清气泡：⟦CLARIFY⟧ 标记 → 左侧品牌色细条（追问身份）；是最后一条时再加「等你回答」。
    val isClarify = waitingForInput || remember(msg.content) { msg.content.contains("⟦CLARIFY⟧") }
    val answerOptions = remember(msg.suggestions) { inputOptions(msg.suggestions) }
    val displayText = remember(msg.content) {
        msg.content
            .replace(Regex("⟦CONFIRM\\|[a-z_]+\\|[\\s\\S]+?⟧"), "")
            .replace(Regex("⟦TASKS:[\\s\\S]+?⟧"), "")
            .replace(Regex("⟦MEM:[\\s\\S]+?⟧"), "")
            .replace("⟦CLARIFY⟧", "")
            .trim()
    }
    var planActed by remember(msg.id) { mutableStateOf(false) }
    var feedbackDialog by remember(msg.id) { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 5.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = CircleShape, modifier = Modifier.size(28.dp)) {
                HashMascot(Modifier.padding(4.dp).size(20.dp))
            }
            Spacer(Modifier.width(8.dp))
            Text("HashMM", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
            if ((waitingForInput || waitingForApproval) && !streaming) {
                Spacer(Modifier.width(8.dp))
                Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(50)) {
                    Text(if (waitingForApproval) "等待工具批准" else "需要你的输入", fontSize = 10.sp, fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        modifier = Modifier.padding(horizontal = 7.dp, vertical = 2.dp))
                }
            } else if (inputResolved) {
                Spacer(Modifier.width(8.dp))
                Text("已回复", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (streaming) {
                Spacer(Modifier.width(8.dp))
                TypingDots(color = MaterialTheme.colorScheme.onSurfaceVariant, dotSize = 5.dp)
            }
        }
        Spacer(Modifier.height(6.dp))
        val haptic2 = LocalHapticFeedback.current
        var menuOpen by remember(msg.id) { mutableStateOf(false) }
        Box {
            Row(Modifier.height(IntrinsicSize.Min)) {
            if (isClarify) {
                Box(Modifier.padding(top = 4.dp, bottom = 4.dp).width(3.dp).fillMaxHeight()
                    .background(MaterialTheme.colorScheme.primary, RoundedCornerShape(2.dp)))
            }
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(topStart = 4.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 16.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)),
                modifier = Modifier.fillMaxWidth().padding(start = if (isClarify) 4.dp else 6.dp)
                    .pointerInput(msg.content, streaming) {
                        if (!streaming) detectTapGestures(onLongPress = {
                            haptic2.performHapticFeedback(HapticFeedbackType.LongPress)
                            menuOpen = true
                        })
                    },
            ) {
                Box(Modifier.padding(horizontal = 14.dp, vertical = 11.dp)) {
                    val text = displayText
                    when {
                        text.isBlank() && streaming -> TypingDots(color = MaterialTheme.colorScheme.onSurfaceVariant)
                        text.isBlank() -> Text("（无内容）", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        streaming -> Text(text, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface, lineHeight = 22.sp)
                        else -> MarkdownText(text, color = MaterialTheme.colorScheme.onSurface, fontSize = 15)
                    }
                }
            }
            }
            MsgActionsMenu(menuOpen, { menuOpen = false }, msg.content, onQuote, onRegenerate)
        }
        if (waitingForInput || inputResolved) {
            Spacer(Modifier.height(8.dp))
            Surface(
                color = if (waitingForInput) MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.42f)
                        else MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(14.dp),
                border = BorderStroke(
                    1.dp,
                    if (waitingForInput) MaterialTheme.colorScheme.primary.copy(alpha = 0.55f)
                    else MaterialTheme.colorScheme.outlineVariant,
                ),
                modifier = Modifier.fillMaxWidth().padding(start = 6.dp),
            ) {
                Column(Modifier.padding(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            if (waitingForInput) "任务已安全暂停" else "已收到你的补充",
                            fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                        Spacer(Modifier.weight(1f))
                        Text(
                            if (waitingForInput) "任意端可继续" else "等待已解除",
                            fontSize = 10.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (waitingForInput && answerOptions.isNotEmpty() && onInputAnswer != null) {
                        Spacer(Modifier.height(8.dp))
                        answerOptions.forEach { option ->
                            OutlinedButton(
                                onClick = { onInputAnswer(option) },
                                shape = RoundedCornerShape(10.dp),
                                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 5.dp),
                                modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                            ) {
                                Text(option, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    } else if (waitingForInput) {
                        Spacer(Modifier.height(5.dp))
                        Text("在下方直接回复，任务会从这里继续。", fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        if (toolApproval != null) {
            ToolApprovalCard(toolApproval, onToolApproval)
        }
        if (tasksJson != null) TaskPanelCard(tasksJson, onCancelTask)
        if (memJson != null) MemoryCard(memJson, onMemAction)
        // plan 模式确认：危险命令的方案 → 确认/取消（确认后下发已批准命令，电脑端才执行）
        if (planKind != null && planPayload != null && onConfirmPlan != null && !planActed) {
            Spacer(Modifier.height(8.dp))
            Row(Modifier.padding(start = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { planActed = true; onConfirmPlan(planKind, planPayload) },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                ) { Text("确认执行") }
                OutlinedButton(onClick = { planActed = true }) { Text("取消") }
            }
        } else if (planKind != null && planActed) {
            Spacer(Modifier.height(4.dp))
            Text("已处理", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 8.dp))
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
                if (onFeedback != null) {
                    Spacer(Modifier.width(2.dp))
                    Icon(
                        Icons.Outlined.ThumbUp,
                        contentDescription = "有帮助",
                        tint = if (msg.feedback == "up") MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(27.dp).clip(CircleShape).clickable {
                            onFeedback(msg.id, if (msg.feedback == "up") "clear" else "up", "", "")
                        }.padding(6.dp),
                    )
                    Icon(
                        Icons.Outlined.ThumbDown,
                        contentDescription = "需要改进",
                        tint = if (msg.feedback == "down") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(27.dp).clip(CircleShape).clickable {
                            if (msg.feedback == "down") onFeedback(msg.id, "clear", "", "")
                            else feedbackDialog = true
                        }.padding(6.dp),
                    )
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
        if (!streaming && msg.runManifest != null) CompletionEvidenceCard(msg.runManifest)
    }
    if (feedbackDialog && onFeedback != null) {
        FeedbackReasonDialog(
            onDismiss = { feedbackDialog = false },
            onSubmit = { reason, comment ->
                feedbackDialog = false
                onFeedback(msg.id, "down", reason, comment)
            },
        )
    }
}

internal val feedbackReasons = listOf(
    "incorrect" to "事实或结论错误",
    "unsupported" to "证据或引用不足",
    "retrieval_miss" to "漏掉应有资料",
    "wrong_tool" to "工具选择、参数或顺序错误",
    "incomplete" to "任务没有真正完成",
    "instruction_miss" to "没有遵守要求或上下文",
    "unsafe" to "安全、权限或隐私风险",
    "too_slow" to "步骤、延迟或成本过高",
    "other" to "其他问题",
)

internal fun feedbackReasonLabel(code: String): String =
    feedbackReasons.firstOrNull { it.first == code }?.second ?: "请选择问题类型"

@Composable
private fun FeedbackReasonDialog(onDismiss: () -> Unit, onSubmit: (String, String) -> Unit) {
    var reason by remember { mutableStateOf("") }
    var comment by remember { mutableStateOf("") }
    var menu by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("这条回答哪里需要改进？", fontWeight = FontWeight.SemiBold) },
        text = {
            Column {
                Text(
                    "反馈会连同真实运行证据进入待复核队列，不会自动把错误回答当成标准答案。",
                    fontSize = 12.sp,
                    lineHeight = 18.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                Box {
                    OutlinedButton(onClick = { menu = true }, modifier = Modifier.fillMaxWidth()) {
                        Text(feedbackReasonLabel(reason), modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Icon(Icons.Outlined.ExpandMore, null, Modifier.size(16.dp))
                    }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        feedbackReasons.forEach { (code, label) ->
                            DropdownMenuItem(
                                text = { Text(label, fontSize = 13.sp) },
                                onClick = { reason = code; menu = false },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = comment,
                    onValueChange = { comment = it.take(1000) },
                    label = { Text("补充说明（可选）") },
                    placeholder = { Text("具体指出错在哪里，便于复现和修复") },
                    minLines = 3,
                    maxLines = 5,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
        confirmButton = {
            Button(onClick = { onSubmit(reason, comment) }, enabled = reason.isNotBlank()) {
                Text("提交并进入复核")
            }
        },
    )
}

internal data class ToolApprovalSummary(
    val requestId: String,
    val toolName: String,
    val arguments: String,
    val risk: String,
    val status: String,
)

internal fun parseToolApproval(element: JsonElement?): ToolApprovalSummary? {
    val root = element as? JsonObject ?: return null
    val approval = root["approval_request"] as? JsonObject ?: return null
    val requestId = approval["request_id"]?.jsonPrimitive?.contentOrNull.orEmpty()
    val toolName = approval["tool_name"]?.jsonPrimitive?.contentOrNull.orEmpty()
    if (requestId.isBlank() || toolName.isBlank()) return null
    return ToolApprovalSummary(
        requestId = requestId,
        toolName = toolName,
        arguments = approval["arguments"]?.toString().orEmpty().take(4000),
        risk = approval["risk"]?.jsonPrimitive?.contentOrNull.orEmpty(),
        status = approval["status"]?.jsonPrimitive?.contentOrNull ?: "pending",
    )
}

@Composable
private fun ToolApprovalCard(
    approval: ToolApprovalSummary,
    onDecision: ((String, Boolean) -> Unit)?,
) {
    var acted by remember(approval.requestId, approval.status) { mutableStateOf(false) }
    val pending = approval.status == "pending"
    val approved = approval.status == "approved"
    val title = when (approval.status) {
        "pending" -> "需要批准工具操作"
        "approved" -> "操作已批准，等待继续"
        "consumed" -> "批准已使用"
        "declined" -> "操作已拒绝"
        "expired" -> "审批已过期"
        else -> "审批状态已更新"
    }
    Spacer(Modifier.height(8.dp))
    Surface(
        color = if (pending || approved) MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.36f)
                else MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(
            1.dp,
            if (pending || approved) MaterialTheme.colorScheme.primary.copy(alpha = 0.5f)
            else MaterialTheme.colorScheme.outlineVariant,
        ),
        modifier = Modifier.fillMaxWidth().padding(start = 6.dp),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface)
                Spacer(Modifier.weight(1f))
                Text("一次性授权", fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(6.dp))
            Text("工具 ${approval.toolName}${if (approval.risk.isNotBlank()) " · ${approval.risk}" else ""}",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (approval.arguments.isNotBlank() && approval.arguments != "{}") {
                Spacer(Modifier.height(6.dp))
                Surface(
                    color = MaterialTheme.colorScheme.surface,
                    shape = RoundedCornerShape(9.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.7f)),
                ) {
                    Text(
                        approval.arguments,
                        modifier = Modifier.fillMaxWidth().padding(9.dp),
                        fontSize = 10.5.sp,
                        lineHeight = 15.sp,
                        maxLines = 8,
                        overflow = TextOverflow.Ellipsis,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (pending && onDecision != null && !acted) {
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(
                        onClick = { acted = true; onDecision(approval.requestId, false) },
                        shape = RoundedCornerShape(10.dp),
                        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 5.dp),
                    ) { Text("拒绝", fontSize = 12.sp) }
                    Button(
                        onClick = { acted = true; onDecision(approval.requestId, true) },
                        shape = RoundedCornerShape(10.dp),
                        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 5.dp),
                    ) { Text("批准并继续", fontSize = 12.sp) }
                }
            } else if (approved && onDecision != null && !acted) {
                Spacer(Modifier.height(8.dp))
                Button(
                    onClick = { acted = true; onDecision(approval.requestId, true) },
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 14.dp, vertical = 5.dp),
                ) { Text("继续任务", fontSize = 12.sp) }
            } else if (acted) {
                Spacer(Modifier.height(5.dp))
                Text("正在同步审批状态", fontSize = 10.5.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

internal data class CompletionSummary(
    val status: String,
    val summary: String,
    val nextAction: String,
    val passed: Int,
    val failed: Int,
    val notEvaluable: Int,
    val graphStatus: String,
    val graphNodes: Int,
    val graphEdges: Int,
    val graphBlockers: Int,
    val graphNextAction: String,
    val frontierUnresolved: Int,
    val frontierReady: Int,
    val frontierScopeBlocked: Int,
    val frontierNextAction: String,
    val gateStatus: String,
    val gateCanComplete: Boolean,
    val gateRequired: Int,
    val gatePassed: Int,
    val gateReview: Int,
    val gateFailed: Int,
    val gateNextAction: String,
    val trajectoryTools: Int,
    val trajectoryAgents: Int,
)

internal fun parseCompletionSummary(element: JsonElement): CompletionSummary? {
    val root = element as? JsonObject ?: return null
    val handoff = root["handoff"] as? JsonObject ?: return null
    val graph = root["evidence_graph"] as? JsonObject
    val graphSummary = graph?.get("summary") as? JsonObject
    val graphActions = graph?.get("next_actions") as? JsonArray
    val frontier = root["execution_frontier"] as? JsonObject
    val frontierSummary = frontier?.get("summary") as? JsonObject
    val frontierItems = frontier?.get("items") as? JsonArray
    val firstFrontierItem = frontierItems?.firstOrNull() as? JsonObject
    val gate = root["completion_gate"] as? JsonObject
    val gateSummary = gate?.get("summary") as? JsonObject
    val trajectory = gate?.get("trajectory") as? JsonObject
    val status = handoff["status"]?.jsonPrimitive?.contentOrNull.orEmpty()
    if (status.isBlank()) return null
    return CompletionSummary(
        status = status,
        summary = handoff["summary"]?.jsonPrimitive?.contentOrNull.orEmpty(),
        nextAction = handoff["next_action"]?.jsonPrimitive?.contentOrNull.orEmpty(),
        passed = (handoff["passed_checks"] as? JsonArray)?.size ?: 0,
        failed = (handoff["failed_checks"] as? JsonArray)?.size ?: 0,
        notEvaluable = (handoff["not_evaluable_checks"] as? JsonArray)?.size ?: 0,
        graphStatus = graph?.get("status")?.jsonPrimitive?.contentOrNull.orEmpty(),
        graphNodes = graphSummary?.get("nodes")?.jsonPrimitive?.intOrNull ?: 0,
        graphEdges = graphSummary?.get("edges")?.jsonPrimitive?.intOrNull ?: 0,
        graphBlockers = graphSummary?.get("blockers")?.jsonPrimitive?.intOrNull ?: 0,
        graphNextAction = graphActions?.firstOrNull()?.jsonPrimitive?.contentOrNull.orEmpty(),
        frontierUnresolved = frontierSummary?.get("unresolved")?.jsonPrimitive?.intOrNull ?: 0,
        frontierReady = frontierSummary?.get("ready_routes")?.jsonPrimitive?.intOrNull ?: 0,
        frontierScopeBlocked = frontierSummary?.get("scope_blocked")?.jsonPrimitive?.intOrNull ?: 0,
        frontierNextAction = firstFrontierItem?.get("minimum_action")?.jsonPrimitive?.contentOrNull.orEmpty(),
        gateStatus = gate?.get("status")?.jsonPrimitive?.contentOrNull.orEmpty(),
        gateCanComplete = gate?.get("can_claim_complete")?.jsonPrimitive?.booleanOrNull ?: false,
        gateRequired = gateSummary?.get("required")?.jsonPrimitive?.intOrNull ?: 0,
        gatePassed = gateSummary?.get("passed")?.jsonPrimitive?.intOrNull ?: 0,
        gateReview = (gateSummary?.get("review")?.jsonPrimitive?.intOrNull ?: 0) +
            (gateSummary?.get("missing")?.jsonPrimitive?.intOrNull ?: 0),
        gateFailed = gateSummary?.get("failed")?.jsonPrimitive?.intOrNull ?: 0,
        gateNextAction = gate?.get("next_action")?.jsonPrimitive?.contentOrNull.orEmpty(),
        trajectoryTools = trajectory?.get("tool_calls")?.jsonPrimitive?.intOrNull ?: 0,
        trajectoryAgents = trajectory?.get("agents")?.jsonPrimitive?.intOrNull ?: 0,
    )
}

@Composable
private fun CompletionEvidenceCard(element: JsonElement) {
    val summary = remember(element) { parseCompletionSummary(element) } ?: return
    val authoritativeStatus = summary.gateStatus.ifBlank { summary.status }
    val title = when (authoritativeStatus) {
        "verified", "checks_passed" -> "证据门已开放"
        "delivered_with_limits" -> "已交付，仍待复核"
        "blocked" -> "任务仍受阻"
        "incomplete" -> "任务尚未闭环"
        else -> "完成状态需要处理"
    }
    val accent = when (authoritativeStatus) {
        "verified", "checks_passed" -> Color(0xFF15803D)
        "delivered_with_limits" -> Color(0xFF9A6700)
        else -> MaterialTheme.colorScheme.error
    }
    Spacer(Modifier.height(8.dp))
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.65f)),
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth().padding(start = 6.dp),
    ) {
        Column(Modifier.padding(horizontal = 13.dp, vertical = 11.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(accent))
                Spacer(Modifier.width(7.dp))
                Text(title, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
            }
            if (summary.summary.isNotBlank()) {
                Spacer(Modifier.height(5.dp))
                Text(summary.summary, fontSize = 11.sp, lineHeight = 16.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(6.dp))
            Text(
                if (summary.gateRequired > 0)
                    "必需 ${summary.gateRequired} · 通过 ${summary.gatePassed} · 失败 ${summary.gateFailed} · 待核验 ${summary.gateReview}"
                else "通过 ${summary.passed} · 未通过 ${summary.failed} · 待复核 ${summary.notEvaluable}",
                fontSize = 10.5.sp,
                color = accent,
            )
            if (summary.gateRequired > 0) {
                Spacer(Modifier.height(3.dp))
                Text(
                    "轨迹：${summary.trajectoryTools} 次工具调用 · ${summary.trajectoryAgents} 个协作分工",
                    fontSize = 9.5.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (summary.graphNodes > 0) {
                Spacer(Modifier.height(6.dp))
                Surface(
                    color = MaterialTheme.colorScheme.surface.copy(alpha = 0.7f),
                    shape = RoundedCornerShape(10.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.55f)),
                ) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("任务证据图", fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold)
                            Spacer(Modifier.weight(1f))
                            Text(
                                "${summary.graphNodes} 节点 · ${summary.graphEdges} 连接 · ${summary.graphBlockers} 阻塞",
                                fontSize = 9.5.sp,
                                color = if (summary.graphBlockers > 0) MaterialTheme.colorScheme.error
                                else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (summary.graphBlockers > 0 && summary.graphNextAction.isNotBlank()) {
                            Spacer(Modifier.height(3.dp))
                            Text("下一步：${summary.graphNextAction}", fontSize = 9.5.sp, lineHeight = 14.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            if (summary.frontierUnresolved > 0) {
                Spacer(Modifier.height(6.dp))
                Surface(
                    color = MaterialTheme.colorScheme.surface.copy(alpha = 0.7f),
                    shape = RoundedCornerShape(10.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.55f)),
                ) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("下一工作集", fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold)
                            Spacer(Modifier.weight(1f))
                            Text("${summary.frontierReady} 可行 · ${summary.frontierScopeBlocked} 权限受限",
                                fontSize = 9.5.sp,
                                color = if (summary.frontierScopeBlocked > 0) MaterialTheme.colorScheme.error
                                else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (summary.frontierNextAction.isNotBlank()) {
                            Spacer(Modifier.height(3.dp))
                            Text(summary.frontierNextAction, fontSize = 9.5.sp, lineHeight = 14.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Spacer(Modifier.height(3.dp))
                        Text("路线可用不等于已批准或已执行。", fontSize = 9.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            val finalNextAction = summary.gateNextAction.ifBlank { summary.nextAction }
            if (finalNextAction.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text("下一步：$finalNextAction", fontSize = 10.5.sp, lineHeight = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(3.dp))
            Text("模型自述和模型评分不能替代运行证据或用户验收。", fontSize = 9.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** 一条引用来源（从消息 sources JSON 解析）。 */
internal data class BubbleSource(
    val id: Int,
    val filename: String,
    val page: Int,
    val snippet: String,
    val method: String,
)


internal fun parseSources(el: JsonElement?): List<BubbleSource> {
    val arr = el as? JsonArray ?: return emptyList()
    return arr.mapIndexedNotNull { i, item ->
        val o = item as? JsonObject ?: return@mapIndexedNotNull null
        BubbleSource(
            id = o["id"]?.jsonPrimitive?.intOrNull ?: (i + 1),
            filename = o["filename"]?.jsonPrimitive?.contentOrNull
                ?: o["modality"]?.jsonPrimitive?.contentOrNull ?: "文档",
            page = o["page"]?.jsonPrimitive?.intOrNull ?: -1,
            snippet = o["text"]?.jsonPrimitive?.contentOrNull ?: "",
            method = o["method"]?.jsonPrimitive?.contentOrNull ?: "",
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
                if (s.method == "graph_evidence") {
                    Spacer(Modifier.width(6.dp))
                    Text(
                        "图关联", fontSize = 9.5.sp, fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.clip(RoundedCornerShape(20.dp))
                            .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.10f))
                            .padding(horizontal = 6.dp, vertical = 2.dp),
                    )
                }
            }
            if (expanded && s.snippet.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(s.snippet, fontSize = 12.sp, lineHeight = 17.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun TaskPanelCard(json: String, onCancelTask: ((String) -> Unit)?) {
    val parsed = remember(json) {
        try {
            val o = org.json.JSONObject(json)
            val arr = o.optJSONArray("items") ?: org.json.JSONArray()
            o.optString("rid") to (0 until arr.length()).map { arr.getJSONObject(it) }
        } catch (_: Exception) { "" to emptyList<org.json.JSONObject>() }
    }
    val rid = parsed.first
    val items = parsed.second
    if (items.isEmpty()) return
    var expanded by remember { mutableStateOf(-1) }
    var canceledLocal by remember { mutableStateOf<Set<Int>>(emptySet()) }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(start = 6.dp, top = 4.dp),
    ) {
        Column(Modifier.padding(12.dp)) {
            items.forEach { obj ->
                val label = obj.optString("l")
                val idx = obj.optInt("i")
                val d = obj.optJSONArray("d")
                val steps = if (d == null) emptyList<String>() else (0 until d.length()).map { d.optString(it) }
                val status = if (canceledLocal.contains(idx)) "canceled" else obj.optString("s")
                val statusColor = when (status) { "running" -> MaterialTheme.colorScheme.primary; "done" -> Color(0xFF16A34A); "failed" -> MaterialTheme.colorScheme.error; "paused" -> Color(0xFFD97706); else -> MaterialTheme.colorScheme.onSurfaceVariant }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().clickable { expanded = if (expanded == idx) -1 else idx }.padding(vertical = 5.dp),
                ) {
                    Box(Modifier.size(16.dp), contentAlignment = Alignment.Center) { StatusDot(status) }
                    Spacer(Modifier.width(10.dp))
                    Text(
                        label, fontSize = 13.sp,
                        color = if (status == "pending" || status == "canceled") MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.weight(1f),
                    )
                    Text(statusLabel(status), fontSize = 11.sp, color = statusColor)
                    if (onCancelTask != null && rid.isNotEmpty() && (status == "running" || status == "pending" || status == "paused")) {
                        Spacer(Modifier.width(6.dp))
                        TextButton(onClick = { canceledLocal = canceledLocal + idx; onCancelTask(rid + ":" + idx) }, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp)) {
                            Text("\u53D6\u6D88", fontSize = 11.sp, color = MaterialTheme.colorScheme.error)
                        }
                    }
                    if (steps.isNotEmpty()) { Spacer(Modifier.width(6.dp)); Text(if (expanded == idx) "\u25BE" else "\u25B8", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
                if (expanded == idx && steps.isNotEmpty()) {
                    Column(Modifier.padding(start = 22.dp, bottom = 4.dp)) {
                        steps.forEach { Text("\u00B7 " + it, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    }
                }
            }
        }
    }
}

@Composable
private fun MemoryCard(json: String, onMemAction: ((String, String) -> Unit)?) {
    val dirs = remember(json) {
        try {
            val a = org.json.JSONObject(json).optJSONArray("dirs")
            if (a == null) emptyList() else (0 until a.length()).map { val d = a.getJSONObject(it); d.optString("path") to d.optInt("count") }
        } catch (_: Exception) { emptyList() }
    }
    val recent = remember(json) {
        try {
            val a = org.json.JSONObject(json).optJSONArray("recent")
            if (a == null) emptyList() else (0 until a.length()).map { a.optString(it) }
        } catch (_: Exception) { emptyList() }
    }
    val prefs = remember(json) {
        try {
            val a = org.json.JSONObject(json).optJSONArray("prefs")
            if (a == null) emptyList() else (0 until a.length()).map { a.optString(it) }
        } catch (_: Exception) { emptyList() }
    }
    val fields = remember(json) {
        try {
            val o = org.json.JSONObject(json).optJSONObject("fields") ?: org.json.JSONObject()
            val list = mutableListOf<Pair<String, String>>()
            val ks = o.keys()
            while (ks.hasNext()) { val k = ks.next(); list.add(k to o.optString(k)) }
            list
        } catch (_: Exception) { emptyList() }
    }
    fun fieldLabel(k: String) = when (k) { "downloadDir" -> "\u4E0B\u8F7D\u76EE\u5F55"; "searchEngine" -> "\u9ED8\u8BA4\u641C\u7D22"; "name" -> "\u79F0\u547C"; else -> k }
    fun engineLabel(v: String) = when (v) { "bing" -> "\u5FC5\u5E94"; "google" -> "\u8C37\u6B4C"; "baidu" -> "\u767E\u5EA6"; else -> "\u672A\u6307\u5B9A" }
    var acted by remember(json) { mutableStateOf<Set<String>>(emptySet()) }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(start = 6.dp, top = 4.dp),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text("\u8BBE\u7F6E", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant)
            run {   // 默认搜索引擎：下拉直接改（不用打字）
                val cur = fields.firstOrNull { it.first == "searchEngine" }?.second ?: ""
                var seOpen by remember { mutableStateOf(false) }
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                    Text("\u9ED8\u8BA4\u641C\u7D22\uFF1A", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Box {
                        TextButton(onClick = { seOpen = true }, contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp)) {
                            Text(engineLabel(cur) + " \u25BE", fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
                        }
                        DropdownMenu(expanded = seOpen, onDismissRequest = { seOpen = false }) {
                            listOf("bing" to "\u5FC5\u5E94", "google" to "\u8C37\u6B4C", "baidu" to "\u767E\u5EA6", "" to "\u4E0D\u6307\u5B9A").forEach { opt ->
                                DropdownMenuItem(text = { Text(opt.second, fontSize = 13.sp) }, onClick = {
                                    seOpen = false
                                    if (onMemAction != null) { if (opt.first.isEmpty()) onMemAction("clear_field", "searchEngine") else onMemAction("set_field", "searchEngine=" + opt.first) }
                                })
                            }
                        }
                    }
                }
            }
            fields.filter { it.first != "searchEngine" }.forEach { fld ->
                val k = fld.first; val v = fld.second
                if (!acted.contains("f:" + k)) Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                    Text(fieldLabel(k) + "\uFF1A" + v, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f), maxLines = 1)
                    if (onMemAction != null) {
                        Spacer(Modifier.width(4.dp))
                        TextButton(onClick = { acted = acted + ("f:" + k); onMemAction("clear_field", k) }, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp)) { Text("\u6E05\u9664", fontSize = 11.sp) }
                    }
                }
            }
            Spacer(Modifier.height(6.dp))
            if (prefs.isNotEmpty()) {
                Text("\u504F\u597D / \u5907\u6CE8", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant)
                prefs.forEach { pref ->
                    if (!acted.contains("p:" + pref)) Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                        Text(pref, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f), maxLines = 2)
                        if (onMemAction != null) {
                            Spacer(Modifier.width(4.dp))
                            TextButton(onClick = { acted = acted + ("p:" + pref); onMemAction("forget_pref", pref) }, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp)) { Text("\u5FD8\u8BB0", fontSize = 11.sp) }
                        }
                    }
                }
                Spacer(Modifier.height(6.dp))
            }
            Text("\u5E38\u7528\u76EE\u5F55", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (dirs.isEmpty()) Text("\uFF08\u8FD8\u6CA1\u5B66\u5230\uFF0C\u591A\u7528\u51E0\u6B21\u53D6\u6587\u4EF6\u5C31\u4F1A\u8BB0\u4F4F\uFF09", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            dirs.forEach { (path, count) ->
                if (!acted.contains(path)) Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                    Text(path, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurface, maxLines = 1, modifier = Modifier.weight(1f))
                    Text("\u00D7" + count, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (onMemAction != null) {
                        Spacer(Modifier.width(4.dp))
                        TextButton(onClick = { acted = acted + path; onMemAction("forget", path) }, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp)) { Text("\u5FD8\u8BB0", fontSize = 11.sp) }
                    }
                }
            }
            if (recent.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Text("\u6700\u8FD1\u4EFB\u52A1", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant)
                recent.take(6).forEach { Text("\u00B7 " + it, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1) }
            }
            Spacer(Modifier.height(4.dp))
            Text("\u60F3\u8BA9\u6211\u8BB0\u4F4F\u4EC0\u4E48\uFF0C\u76F4\u63A5\u8BF4\u300C\u8BB0\u4F4F \u2026\u300D\uFF08\u5982\uFF1A\u8BB0\u4F4F \u7528\u5FC5\u5E94\u641C\u7D22\uFF09", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (onMemAction != null && (dirs.isNotEmpty() || recent.isNotEmpty() || prefs.isNotEmpty() || fields.isNotEmpty())) {
                TextButton(onClick = { onMemAction("clear", "") }) { Text("\u6E05\u7A7A\u5168\u90E8\u8BB0\u5FC6", fontSize = 12.sp, color = MaterialTheme.colorScheme.error) }
            }
        }
    }
}

// 专业的状态指示：进行中=细环形进度，其余=语义色圆点（替代 emoji 方块）
@Composable
private fun StatusDot(status: String) {
    when (status) {
        "running" -> CircularProgressIndicator(modifier = Modifier.size(13.dp), strokeWidth = 1.6.dp, color = MaterialTheme.colorScheme.primary)
        "done" -> Box(Modifier.size(9.dp).clip(CircleShape).background(Color(0xFF16A34A)))
        "failed" -> Box(Modifier.size(9.dp).clip(CircleShape).background(MaterialTheme.colorScheme.error))
        "paused" -> Box(Modifier.size(9.dp).clip(CircleShape).background(Color(0xFFD97706)))
        "canceled" -> Box(Modifier.size(9.dp).clip(CircleShape).background(MaterialTheme.colorScheme.outline.copy(alpha = 0.45f)))
        else -> Box(Modifier.size(9.dp).clip(CircleShape).border(1.5.dp, MaterialTheme.colorScheme.outline, CircleShape))
    }
}
private fun statusLabel(status: String) = when (status) {
    "running" -> "\u8FDB\u884C\u4E2D"   // 进行中
    "done" -> "\u5DF2\u5B8C\u6210"      // 已完成
    "failed" -> "\u5931\u8D25"           // 失败
    "paused" -> "\u5F85\u786E\u8BA4"    // 待确认
    "canceled" -> "\u5DF2\u53D6\u6D88"  // 已取消
    else -> "\u7B49\u5F85"               // 等待
}
