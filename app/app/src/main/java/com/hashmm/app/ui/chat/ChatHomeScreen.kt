package com.hashmm.app.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Mic
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.platform.LocalContext
import android.content.Intent
import android.speech.RecognizerIntent
import android.widget.Toast
import androidx.compose.material.icons.outlined.Code
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Send
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.ui.components.HashMascotHero
import com.hashmm.app.ui.theme.Accent
import com.hashmm.app.ui.theme.AvatarGradient
import kotlinx.coroutines.launch

/**
 * 「对话」主页 —— Marvis 对话优先布局，无内层 Scaffold：
 *  顶栏 Row(☰ | HashMM + 账号 | 远程 + 记录) · 中部欢迎 · 底部圆角输入条(imePadding 贴键盘) · 左侧抽屉。
 */
@Composable
fun ChatHomeScreen(
    email: String?,
    onOpenConversation: (String) -> Unit,
    onOpenRemote: () -> Unit,
    onSendTask: (String) -> Unit,
    viewModel: ChatListViewModel = hiltViewModel(),
    chatVm: ChatHomeViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val chat by chatVm.ui.collectAsStateWithLifecycle()
    val viewerUrl by chatVm.viewerUrl.collectAsStateWithLifecycle()
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    var input by remember { mutableStateOf("") }
    var quoted by remember { mutableStateOf<String?>(null) }   // @引用：长按某条消息→引用，发送时带上
    var searching by remember { mutableStateOf(false) }        // 消息搜索：在当前会话内筛选
    var searchQuery by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    val haptic = LocalHapticFeedback.current
    var showActions by remember { mutableStateOf(false) }

    // 新消息到来自动滚到底（搜索筛选时不滚，避免越界）
    androidx.compose.runtime.LaunchedEffect(chat.messages.size, chat.messages.lastOrNull()?.content, searching, searchQuery) {
        if (chat.messages.isNotEmpty() && !(searching && searchQuery.isNotBlank())) listState.animateScrollToItem(chat.messages.size - 1)
    }

    fun submit() {
        val t = input.trim()
        if (t.isNotEmpty()) {
            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
            val finalText = quoted?.let { "引用上文：「${it.take(200)}」\n\n$t" } ?: t
            chatVm.send(finalText); input = ""; quoted = null
        }
    }

    androidx.compose.runtime.CompositionLocalProvider(
        LocalFileOpener provides { filename, downloadUrl -> chatVm.openFile(downloadUrl, filename) }
    ) {
    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet(
                drawerContainerColor = MaterialTheme.colorScheme.surface,
                modifier = Modifier.fillMaxWidth(0.82f),
            ) {
                Spacer(Modifier.height(20.dp))
                Text(
                    "HashMM",
                    fontSize = 26.sp, fontWeight = FontWeight.ExtraBold,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(horizontal = 22.dp),
                )
                Spacer(Modifier.height(18.dp))
                Surface(
                    shape = RoundedCornerShape(14.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                        .clickable { scope.launch { drawerState.close() }; input = ""; chatVm.newChat() },
                ) {
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 14.dp),
                        horizontalArrangement = Arrangement.Center,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Outlined.AddComment, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("新建对话", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                    }
                }
                Spacer(Modifier.height(16.dp))
                Text(
                    "历史对话",
                    fontSize = 12.sp, fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 22.dp),
                )
                Spacer(Modifier.height(8.dp))
                if (email == null) {
                    Box(Modifier.fillMaxWidth().padding(vertical = 40.dp), contentAlignment = Alignment.Center) {
                        Text("登录后查看历史对话", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                } else if (ui.conversations.isEmpty()) {
                    Box(Modifier.fillMaxWidth().padding(vertical = 40.dp), contentAlignment = Alignment.Center) {
                        Text("暂无对话记录", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                } else {
                    LazyColumn(Modifier.fillMaxWidth()) {
                        items(ui.conversations, key = { it.id }) { conv ->
                            DrawerConvRow(conv) {
                                scope.launch { drawerState.close() }
                                chatVm.openConversation(conv.id)
                            }
                        }
                    }
                }
            }
        },
    ) {
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
            // 顶栏
            Row(
                Modifier.fillMaxWidth().padding(start = 6.dp, end = 6.dp, top = 6.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = { scope.launch { drawerState.open() } }) {
                    Icon(Icons.Outlined.Menu, contentDescription = "菜单", tint = MaterialTheme.colorScheme.onSurface)
                }
                Column(Modifier.weight(1f)) {
                    Text("HashMM", fontSize = 18.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(7.dp).clip(CircleShape).background(if (email != null) Accent else MaterialTheme.colorScheme.onSurfaceVariant))
                        Spacer(Modifier.width(5.dp))
                        Text(
                            email ?: "未登录",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.width(180.dp),
                        )
                        Icon(Icons.Outlined.KeyboardArrowDown, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(16.dp))
                    }
                }
                if (chat.convId != null) {
                    IconButton(onClick = { searching = !searching; if (!searching) searchQuery = "" }) {
                        Icon(Icons.Outlined.Search, contentDescription = "搜索消息", tint = if (searching) Accent else MaterialTheme.colorScheme.onSurface)
                    }
                    IconButton(onClick = { chatVm.newChat(); input = ""; searching = false; searchQuery = "" }) {
                        Icon(Icons.Outlined.AddComment, contentDescription = "新对话", tint = MaterialTheme.colorScheme.onSurface)
                    }
                }
                IconButton(onClick = onOpenRemote) {
                    Icon(Icons.Outlined.Computer, contentDescription = "远程", tint = MaterialTheme.colorScheme.onSurface)
                }
                IconButton(onClick = { scope.launch { drawerState.open() } }) {
                    Icon(Icons.Outlined.History, contentDescription = "对话记录", tint = MaterialTheme.colorScheme.onSurface)
                }
            }

            // 消息搜索栏：顶栏点放大镜后出现，在当前会话内按关键词筛选
            if (searching && chat.convId != null) {
                Surface(
                    color = MaterialTheme.colorScheme.surface,
                    shape = RoundedCornerShape(12.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp).padding(bottom = 4.dp),
                ) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Search, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Box(Modifier.weight(1f)) {
                            if (searchQuery.isEmpty()) Text("搜索本会话消息…", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            BasicTextField(
                                value = searchQuery, onValueChange = { searchQuery = it },
                                textStyle = TextStyle(color = MaterialTheme.colorScheme.onSurface, fontSize = 14.sp),
                                cursorBrush = SolidColor(MaterialTheme.colorScheme.onSurface),
                                singleLine = true, modifier = Modifier.fillMaxWidth(),
                            )
                        }
                        if (searchQuery.isNotBlank()) {
                            Icon(Icons.Outlined.Close, "清空", tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(18.dp).clip(CircleShape).clickable { searchQuery = "" })
                        }
                    }
                }
            }

            // 中部：空态显示小哈 hero；进入会话后内联展示消息
            if (chat.convId == null) {
                Column(
                    Modifier.weight(1f).fillMaxWidth().padding(horizontal = 32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    HashMascotHero(Modifier.size(168.dp))
                    Spacer(Modifier.height(16.dp))
                    Text("我是小哈", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "查资料、读文档、写文案、跑任务，把要做的事交给我",
                        fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(22.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        StarterCard("基于知识库回答", "带出处地回答专业问题", Modifier.weight(1f)) {
                            chatVm.send("基于知识库，回答我接下来的问题，并标注出处。"); input = ""
                        }
                        StarterCard("提炼上传的资料", "把文档要点整理成清单", Modifier.weight(1f)) {
                            chatVm.send("把知识库里的资料提炼成结构化要点清单。"); input = ""
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        StarterCard("深度调研一个主题", "多源检索后综合成报告", Modifier.weight(1f)) { input = "帮我深度调研一个主题：" }
                        StarterCard("写一段代码", "实现并保存成可运行文件", Modifier.weight(1f)) { input = "帮我写一段代码，实现：" }
                    }
                }
            } else {
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    if (chat.loadingHistory && chat.messages.isEmpty()) {
                        androidx.compose.material3.CircularProgressIndicator(Modifier.align(Alignment.Center))
                    } else {
                        val visibleMessages = if (searching && searchQuery.isNotBlank())
                            chat.messages.filter { it.content.contains(searchQuery, ignoreCase = true) }
                        else chat.messages
                        if (searching && searchQuery.isNotBlank() && visibleMessages.isEmpty()) {
                            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                Text("没有匹配「$searchQuery」的消息", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        LazyColumn(
                            Modifier.fillMaxSize(),
                            state = listState,
                            contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 10.dp),
                        ) {
                            items(visibleMessages, key = { it.id }) { msg ->
                                val isLast = msg.id == chat.messages.lastOrNull()?.id && !(searching && searchQuery.isNotBlank())
                                MessageBubble(
                                    msg,
                                    onRegenerate = if (isLast && msg.role == "assistant" && !chat.sending) {
                                        { chatVm.regenerate() }
                                    } else null,
                                    onQuote = { q -> quoted = q },
                                )
                            }
                        }
                    }
                    chat.error?.let { err ->
                        Surface(
                            color = MaterialTheme.colorScheme.errorContainer, shape = RoundedCornerShape(10.dp),
                            modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp)
                                .clickable { chatVm.retryLast() },
                        ) {
                            Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(err, color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 12.sp)
                                Spacer(Modifier.width(8.dp))
                                Text("点击重试", color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                    // 回到底部：往上翻看历史时出现，点一下回到最新消息（长对话体验）
                    if (listState.canScrollForward) {
                        val scrollScope = rememberCoroutineScope()
                        Surface(
                            shape = CircleShape,
                            color = MaterialTheme.colorScheme.surface,
                            shadowElevation = 3.dp,
                            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.2f)),
                            modifier = Modifier.align(Alignment.BottomEnd).padding(end = 14.dp, bottom = 14.dp)
                                .size(40.dp)
                                .clickable {
                                    scrollScope.launch {
                                        if (chat.messages.isNotEmpty()) listState.animateScrollToItem(chat.messages.size - 1)
                                    }
                                },
                        ) {
                            Icon(
                                Icons.Outlined.KeyboardArrowDown, contentDescription = "回到底部",
                                tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.padding(9.dp),
                            )
                        }
                    }
                }
            }

            // @引用 横幅：长按某条消息选「引用」后出现，发送时带上，× 可取消
            quoted?.let { q ->
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp).padding(bottom = 2.dp),
                ) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.width(3.dp).height(26.dp).clip(RoundedCornerShape(2.dp)).background(MaterialTheme.colorScheme.primary))
                        Spacer(Modifier.width(8.dp))
                        Text(
                            "引用：" + q.replace("\n", " ").take(46) + if (q.length > 46) "…" else "",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                        )
                        Icon(
                            Icons.Outlined.Close, contentDescription = "取消引用",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(18.dp).clip(CircleShape).clickable { quoted = null },
                        )
                    }
                }
            }
            // 底部输入条
            InputBar(value = input, onValueChange = { input = it }, sending = chat.sending,
                onSend = { submit() }, onStop = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); chatVm.stop() }, onAttach = { showActions = true })
        }
    }
    if (showActions) {
        QuickActionsSheet(
            onDismiss = { showActions = false },
            onFill = { p -> input = p; showActions = false },
            onSendNow = { p -> chatVm.send(p); input = ""; showActions = false },
            onNewChat = { chatVm.newChat(); input = ""; showActions = false },
        )
    }
    viewerUrl?.let { InAppFileViewer(url = it, onClose = { chatVm.closeViewer() }) }
    }
}

/** 「+」快捷操作面板（大厂同款：从底部滑出，常用动作一键就位）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun QuickActionsSheet(
    onDismiss: () -> Unit,
    onFill: (String) -> Unit,
    onSendNow: (String) -> Unit,
    onNewChat: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 8.dp).padding(bottom = 18.dp)) {
            Text("快捷操作", fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 12.dp, top = 4.dp, bottom = 8.dp))
            QuickActionRow(Icons.Outlined.Search, "深度调研一个主题", "多源检索后综合成报告") { onFill("帮我深度调研一个主题：") }
            QuickActionRow(Icons.Outlined.Code, "写一段代码", "实现并保存成可运行文件") { onFill("帮我写一段代码，实现：") }
            QuickActionRow(Icons.Outlined.Description, "整理成要点清单", "把资料提炼成结构化要点") { onSendNow("把知识库里的资料提炼成结构化要点清单。") }
            QuickActionRow(Icons.Outlined.Computer, "把电脑里的文件发我", "从桌面客户端取文件") { onFill("把电脑上的") }
            QuickActionRow(Icons.Outlined.AddComment, "开启新对话", "清空当前、重新开始") { onNewChat() }
        }
    }
}

@Composable
private fun QuickActionRow(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, sub: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(38.dp).clip(RoundedCornerShape(11.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
        }
        Spacer(Modifier.width(13.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(2.dp))
            Text(sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun StarterCard(title: String, sub: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
        modifier = modifier.clickable(onClick = onClick),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 11.dp)) {
            Text(title, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(3.dp))
            Text(sub, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 14.sp)
        }
    }
}

@Composable
private fun InputBar(value: String, onValueChange: (String) -> Unit, sending: Boolean, onSend: () -> Unit, onStop: () -> Unit, onAttach: () -> Unit) {
    val ctx = LocalContext.current
    val curValue by rememberUpdatedState(value)
    // 语音输入：唤起系统语音转文字，结果追加到输入框（minSdk26 自带，无需录音权限）
    val voiceLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val spoken = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
        if (!spoken.isNullOrBlank()) {
            onValueChange(if (curValue.isBlank()) spoken else "$curValue $spoken")
        }
    }
    fun launchVoice() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PROMPT, "请开始说话…")
        }
        try { voiceLauncher.launch(intent) }
        catch (_: Exception) { Toast.makeText(ctx, "此设备未提供语音输入服务", Toast.LENGTH_SHORT).show() }
    }
    Surface(color = MaterialTheme.colorScheme.background) {
        Box(Modifier.fillMaxWidth().imePadding().padding(horizontal = 14.dp, vertical = 10.dp)) {
            Surface(
                shape = RoundedCornerShape(26.dp),
                color = MaterialTheme.colorScheme.surface,
                modifier = Modifier.fillMaxWidth()
                    .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.5f), RoundedCornerShape(26.dp)),
            ) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        Modifier.size(36.dp).clip(CircleShape).clickable(onClick = onAttach),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(
                            Icons.Outlined.Add, contentDescription = "快捷操作",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(24.dp),
                        )
                    }
                    Box(Modifier.weight(1f).padding(vertical = 8.dp)) {
                        if (value.isEmpty()) {
                            Text(if (sending) "Agent 正在回复…" else "请输入问题，交给小哈", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        BasicTextField(
                            value = value,
                            onValueChange = onValueChange,
                            textStyle = TextStyle(color = MaterialTheme.colorScheme.onSurface, fontSize = 15.sp),
                            cursorBrush = SolidColor(MaterialTheme.colorScheme.onSurface),
                            maxLines = 5,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    // 语音输入按钮：未在发送时显示，点一下说话
                    if (!sending) {
                        Box(
                            Modifier.size(36.dp).clip(CircleShape).clickable { launchVoice() },
                            contentAlignment = Alignment.Center,
                        ) {
                            Icon(
                                Icons.Outlined.Mic, contentDescription = "语音输入",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(22.dp),
                            )
                        }
                    }
                    val active = value.isNotBlank() && !sending
                    val interaction = remember { MutableInteractionSource() }
                    val pressed by interaction.collectIsPressedAsState()
                    val scale by animateFloatAsState(if (pressed && (active || sending)) 0.86f else 1f, label = "sendScale")
                    Box(
                        Modifier.size(38.dp).graphicsLayer { scaleX = scale; scaleY = scale }
                            .clip(CircleShape)
                            .background(if (active || sending) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant)
                            .clickable(enabled = active || sending, interactionSource = interaction, indication = null, onClick = { if (sending) onStop() else onSend() }),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (sending) {
                            // 停止生成：白色小方块（点一下停止当前回复）
                            Box(Modifier.size(13.dp).clip(RoundedCornerShape(3.dp)).background(Color.White))
                        } else {
                            Icon(
                                Icons.Outlined.Send, contentDescription = "发送",
                                tint = if (active) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun DrawerConvRow(conv: ChatConversation, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 18.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(34.dp).clip(RoundedCornerShape(10.dp)).background(AvatarGradient),
            contentAlignment = Alignment.Center,
        ) {
            Text(conv.title.trim().take(1).ifBlank { "话" }, color = Color.White, fontWeight = FontWeight.Bold, fontSize = 14.sp)
        }
        Spacer(Modifier.width(12.dp))
        Text(
            conv.title.ifBlank { "新对话" },
            fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
    }
}
