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
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.material.icons.outlined.FlashOn
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.DisposableEffect
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
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.data.sync.ConversationSyncState
import com.hashmm.app.ui.components.HashMascotHero
import com.hashmm.app.ui.theme.Accent
import com.hashmm.app.ui.theme.AppFont
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
    onComputerTask: (String, String) -> Unit = { _, _ -> },
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
    var drawerQuery by remember { mutableStateOf("") }

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
                Spacer(Modifier.height(14.dp))
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("对话", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
                        Text(
                            when (ui.syncState) {
                                ConversationSyncState.FRESH, ConversationSyncState.VERIFIED_EMPTY -> "已与工作区同步"
                                ConversationSyncState.OFFLINE_CACHED -> "离线 · 显示上次同步记录"
                                ConversationSyncState.SIGNED_OUT -> "登录后同步历史"
                                else -> if (ui.syncing) "正在连接工作区" else "工作区暂不可用"
                            },
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (ui.syncing) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                }
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.72f),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                ) {
                    Row(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.Search, contentDescription = null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(8.dp))
                        BasicTextField(
                            value = drawerQuery,
                            onValueChange = { drawerQuery = it },
                            singleLine = true,
                            textStyle = TextStyle(fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurface),
                            cursorBrush = SolidColor(Accent),
                            modifier = Modifier.weight(1f),
                            decorationBox = { inner ->
                                if (drawerQuery.isBlank()) Text("搜索历史对话", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                inner()
                            },
                        )
                        if (drawerQuery.isNotBlank()) IconButton(onClick = { drawerQuery = "" }, modifier = Modifier.size(28.dp)) {
                            Icon(Icons.Outlined.Close, contentDescription = "清除搜索", modifier = Modifier.size(16.dp))
                        }
                    }
                }
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = Accent.copy(alpha = 0.10f),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                        .clickable { scope.launch { drawerState.close() }; input = ""; chatVm.newChat() },
                ) {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Outlined.AddComment, contentDescription = null, tint = Accent, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(10.dp))
                        Text("新建对话", fontSize = AppFont.body, fontWeight = FontWeight.SemiBold, color = Accent)
                    }
                }
                Spacer(Modifier.height(10.dp))
                if (email == null) {
                    Box(Modifier.fillMaxWidth().padding(vertical = 40.dp), contentAlignment = Alignment.Center) {
                        Text("登录后查看历史对话", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                } else if (ui.loading && ui.conversations.isEmpty()) {
                    Box(Modifier.fillMaxWidth().padding(vertical = 40.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                    }
                } else {
                    val filteredDrawer = ui.conversations.filter {
                        drawerQuery.isBlank() || it.title.contains(drawerQuery.trim(), ignoreCase = true)
                    }
                    if (filteredDrawer.isEmpty()) {
                        Column(
                            Modifier.fillMaxWidth().padding(horizontal = 28.dp, vertical = 40.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Text(
                                when {
                                    drawerQuery.isNotBlank() -> "没有找到相关对话"
                                    ui.syncState == ConversationSyncState.VERIFIED_EMPTY -> "暂无历史对话"
                                    else -> ui.error ?: "暂时无法读取历史对话"
                                },
                                fontSize = 14.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                textAlign = TextAlign.Center,
                            )
                            if (ui.syncState != ConversationSyncState.VERIFIED_EMPTY) {
                                Spacer(Modifier.height(12.dp))
                                Text(
                                    "重新连接",
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    color = Accent,
                                    modifier = Modifier.clip(RoundedCornerShape(8.dp)).clickable { viewModel.refresh() }.padding(8.dp),
                                )
                            }
                        }
                    } else LazyColumn(Modifier.fillMaxWidth()) {
                        drawerConversationGroups(filteredDrawer).forEach { (label, conversations) ->
                            item(key = "group-$label") {
                                Text(
                                    label,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(start = 20.dp, end = 16.dp, top = 12.dp, bottom = 5.dp),
                                )
                            }
                            items(conversations, key = { it.id }) { conv ->
                                DrawerConvRow(conv, selected = chat.convId == conv.id) {
                                    drawerQuery = ""
                                scope.launch { drawerState.close() }
                                chatVm.openConversation(conv.id)
                            }
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
                    Text("HashMM", fontSize = AppFont.sectionTitle, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
                    // V251 执行体切换（Marvis「我的手机 / Augety」同款心智）：状态点 + 执行体名 + 箭头，
                    // 点开下拉三档；全局持久，会话详情页共用同一值。
                    var execMenu by remember { mutableStateOf(false) }
                    Box {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.clip(RoundedCornerShape(6.dp))
                                .clickable { execMenu = true }.padding(vertical = 1.dp),
                        ) {
                            Box(Modifier.size(7.dp).clip(CircleShape).background(when {
                                chat.execMode == "direct" -> com.hashmm.app.ui.theme.BrandRed
                                ui.syncState == ConversationSyncState.FRESH || ui.syncState == ConversationSyncState.VERIFIED_EMPTY -> Color(0xFF34C759)
                                ui.syncing -> Color(0xFFFFA000)
                                else -> MaterialTheme.colorScheme.onSurfaceVariant
                            }))
                            Spacer(Modifier.width(5.dp))
                            Text(
                                when (chat.execMode) {
                                    "direct" -> "手机快速回答"
                                    "backend" -> "服务器工作区"
                                    else -> when (ui.syncState) {
                                        ConversationSyncState.FRESH, ConversationSyncState.VERIFIED_EMPTY -> "智能选择 · 后端在线"
                                        ConversationSyncState.OFFLINE_CACHED -> "智能选择 · 离线缓存"
                                        else -> if (ui.syncing) "智能选择 · 正在连接" else "智能选择 · 需检查连接"
                                    }
                                },
                                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                            Icon(Icons.Outlined.KeyboardArrowDown, contentDescription = "切换执行体",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(16.dp))
                        }
                        DropdownMenu(expanded = execMenu, onDismissRequest = { execMenu = false }) {
                            listOf(
                                Triple("auto", "智能选择（推荐）", "优先使用服务器；仅在能力等价时手机兜底"),
                                Triple("backend", "服务器工作区", "使用 RAG、项目、工具和持久化历史"),
                                Triple("direct", "手机快速回答", "不使用服务器知识库、文件和工具"),
                            ).forEach { (k, t, d) ->
                                DropdownMenuItem(
                                    text = {
                                        Column {
                                            Text(t, fontSize = 14.sp,
                                                fontWeight = if (chat.execMode == k) FontWeight.SemiBold else FontWeight.Normal)
                                            Text(d, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        }
                                    },
                                    trailingIcon = {
                                        if (chat.execMode == k) Icon(Icons.Outlined.Check,
                                            contentDescription = null, modifier = Modifier.size(16.dp))
                                    },
                                    onClick = { chatVm.setExecMode(k); execMenu = false },
                                )
                            }
                        }
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

            // V1700：直连只在取得后端写入回执后显示“已进入工作区”。
            if (chat.directMode) {
                Surface(color = com.hashmm.app.ui.theme.WarmBeige, modifier = Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.FlashOn, null,
                            tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(15.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(
                            if (chat.directPersisted)
                                "手机快速回答 · 本轮已写入服务器工作区"
                            else
                                "手机快速回答 · 暂未写入服务器，恢复连接后将继续同步",
                            fontSize = 12.sp, color = com.hashmm.app.ui.theme.OnWarmBeige,
                            fontWeight = FontWeight.Medium)
                    }
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
                    HashMascotHero(Modifier.size(148.dp))
                    Spacer(Modifier.height(16.dp))
                    Text("我是小哈", fontSize = AppFont.sectionTitle, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "查资料、读文档、写文案、跑任务，把要做的事交给我",
                        fontSize = AppFont.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant,
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
                                    onInputAnswer = { answer -> chatVm.send(answer) },
                                    onToolApproval = { requestId, approve ->
                                        chatVm.decideToolApproval(requestId, approve)
                                    },
                                    onFeedback = if (!chat.directMode) {
                                        { messageId, rating, reason, comment ->
                                            chatVm.submitMessageFeedback(messageId, rating, reason, comment)
                                        }
                                    } else null,
                                )
                            }
                            if (chat.sending && listOf(chat.taskContract, chat.liveTodo, chat.liveProgress).any { it.isNotBlank() }) {
                                item(key = "live-task-status") {
                                    LiveTaskStatusCard(
                                        taskContract = chat.taskContract,
                                        todo = chat.liveTodo,
                                        progress = chat.liveProgress,
                                        stepCount = chat.liveStepCount,
                                    )
                                }
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
                                .clip(CircleShape)
                                .clickable(
                                    interactionSource = remember { androidx.compose.foundation.interaction.MutableInteractionSource() },
                                    // V243: rememberRipple 已废弃 → material3 ripple()（新 Indication API，非 Composable，无需 remember）
                                    indication = androidx.compose.material3.ripple(bounded = false, radius = 20.dp),
                                ) {
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
                turnSteerable = chat.turnSteerable, steering = chat.steering, interrupting = chat.interrupting,
                onSend = { submit() }, onStop = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); chatVm.stop() }, onAttach = { showActions = true },
                onTranscribe = { f, cb -> chatVm.transcribeAudio(f, cb) })
        }
    }
    if (showActions) {
        QuickActionsSheet(
            onDismiss = { showActions = false },
            onFill = { p -> input = p; showActions = false },
            onSendNow = { p -> chatVm.send(p); input = ""; showActions = false },
            onNewChat = { chatVm.newChat(); input = ""; showActions = false },
            onComputer = { task, kind -> showActions = false; onComputerTask(task, kind) },
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
    onComputer: (String, String) -> Unit = { _, _ -> },
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
            QuickActionRow(Icons.Outlined.Computer, "把电脑里的文件发我", "由电脑客户端执行 · 结果回到对话") { onComputer("最近10个文件", "file") }
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
private fun InputBar(
    value: String, onValueChange: (String) -> Unit, sending: Boolean,
    turnSteerable: Boolean, steering: Boolean, interrupting: Boolean,
    onSend: () -> Unit, onStop: () -> Unit, onAttach: () -> Unit,
    onTranscribe: (java.io.File, (String?) -> Unit) -> Unit = { _, cb -> cb(null) },
) {
    val ctx = LocalContext.current
    val curValue by rememberUpdatedState(value)
    fun appendSpoken(spoken: String?) {
        if (!spoken.isNullOrBlank()) onValueChange(if (curValue.isBlank()) spoken else "${curValue.trimEnd()} $spoken")
    }
    // ── 语音输入（修复"手机未提供语音服务"）：三级真实链路，逐级兜底 ──
    //  A. 端上 SpeechRecognizer（有系统识别服务的机器，免弹窗、带部分结果）
    //  B. 录音 → 上传后端 /api/stt 转写（国产无 GMS 机型的主路径；点一下开始、再点结束）
    //  C. 系统识别弹窗 RecognizerIntent（最后再试一次）
    var listening by remember { mutableStateOf(false) }      // A 进行中
    var recordingB by remember { mutableStateOf(false) }     // B 录音中
    var transcribing by remember { mutableStateOf(false) }   // B 转写中
    var partialText by remember { mutableStateOf("") }
    var recorder by remember { mutableStateOf<android.media.MediaRecorder?>(null) }
    var audioFile by remember { mutableStateOf<java.io.File?>(null) }
    var hasMicPerm by remember {
        mutableStateOf(
            androidx.core.content.ContextCompat.checkSelfPermission(
                ctx, android.Manifest.permission.RECORD_AUDIO
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
        )
    }
    val voiceLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        appendSpoken(result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull())
    }
    fun launchSystemDialog(): Boolean {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PROMPT, "请开始说话…")
        }
        return try { voiceLauncher.launch(intent); true } catch (_: Exception) { false }
    }
    fun stopRecordAndTranscribe() {
        val rec = recorder; val f = audioFile
        recorder = null; recordingB = false
        try { rec?.stop() } catch (_: Exception) {}
        try { rec?.release() } catch (_: Exception) {}
        if (f == null || !f.exists() || f.length() < 800) { audioFile = null; return }
        transcribing = true
        onTranscribe(f) { text ->
            transcribing = false; audioFile = null
            if (text.isNullOrBlank()) {
                Toast.makeText(ctx, "转写没有结果：请确认电脑客户端在线且后端已开启语音转文字", Toast.LENGTH_LONG).show()
            } else appendSpoken(text)
        }
    }
    fun startRecordB(): Boolean {
        return try {
            val f = java.io.File(ctx.cacheDir, "voice_${System.currentTimeMillis()}.m4a")
            @Suppress("DEPRECATION")
            val rec = if (android.os.Build.VERSION.SDK_INT >= 31) android.media.MediaRecorder(ctx)
                      else android.media.MediaRecorder()
            rec.setAudioSource(android.media.MediaRecorder.AudioSource.MIC)
            rec.setOutputFormat(android.media.MediaRecorder.OutputFormat.MPEG_4)
            rec.setAudioEncoder(android.media.MediaRecorder.AudioEncoder.AAC)
            rec.setAudioEncodingBitRate(64000)
            rec.setAudioSamplingRate(16000)
            rec.setOutputFile(f.absolutePath)
            rec.prepare(); rec.start()
            recorder = rec; audioFile = f; recordingB = true
            Toast.makeText(ctx, "正在录音，再点一下麦克风结束", Toast.LENGTH_SHORT).show()
            true
        } catch (_: Exception) { recorder = null; audioFile = null; recordingB = false; false }
    }
    val recognizer = remember {
        if (android.speech.SpeechRecognizer.isRecognitionAvailable(ctx))
            android.speech.SpeechRecognizer.createSpeechRecognizer(ctx) else null
    }
    DisposableEffect(recognizer) {
        recognizer?.setRecognitionListener(object : android.speech.RecognitionListener {
            override fun onReadyForSpeech(params: android.os.Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onError(error: Int) {
                listening = false; partialText = ""
                // 端上识别报错（如无网络/服务忙）→ 立刻切 B 级：录音传后端，不让用户吃闭门羹
                if (hasMicPerm && !recordingB) startRecordB()
            }
            override fun onResults(results: android.os.Bundle?) {
                listening = false; partialText = ""
                appendSpoken(results?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull())
            }
            override fun onPartialResults(partialResults: android.os.Bundle?) {
                partialResults?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                    ?.let { if (it.isNotBlank()) partialText = it }
            }
            override fun onEvent(eventType: Int, params: android.os.Bundle?) {}
        })
        onDispose {
            try { recognizer?.destroy() } catch (_: Exception) {}
            try { recorder?.release() } catch (_: Exception) {}
        }
    }
    val permLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        hasMicPerm = granted
        if (granted) { if (!startRecordB() && !launchSystemDialog()) Toast.makeText(ctx, "启动录音失败，请重试", Toast.LENGTH_SHORT).show() }
        else Toast.makeText(ctx, "需要录音权限才能语音输入", Toast.LENGTH_SHORT).show()
    }
    fun launchVoice() {
        when {
            recordingB -> stopRecordAndTranscribe()                       // 再点=结束录音并转写
            listening -> { try { recognizer?.stopListening() } catch (_: Exception) {} }
            transcribing -> {}
            recognizer != null -> {                                        // A 级：端上识别
                partialText = ""
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                }
                try { listening = true; recognizer.startListening(intent) }
                catch (_: Exception) { listening = false; if (hasMicPerm) startRecordB() else permLauncher.launch(android.Manifest.permission.RECORD_AUDIO) }
            }
            hasMicPerm -> { if (!startRecordB() && !launchSystemDialog())  // B 级：录音传后端
                Toast.makeText(ctx, "本机无语音服务且录音启动失败；可在电脑客户端使用语音", Toast.LENGTH_LONG).show() }
            else -> permLauncher.launch(android.Manifest.permission.RECORD_AUDIO)
        }
    }
    val voiceBusy = listening || recordingB || transcribing
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
                            Text(
                                when {
                                    listening -> if (partialText.isBlank()) "正在聆听…" else partialText
                                    recordingB -> "录音中…再点麦克风结束"
                                    transcribing -> "正在转写…"
                                    sending && turnSteerable -> "追加要求到当前任务"
                                    sending -> "任务正在建立运行通道…"
                                    else -> "请输入问题，交给小哈"
                                },
                                fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1,
                            )
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
                    // 语音输入按钮：未在发送时显示；聆听/录音中高亮，转写中转圈
                    if (!sending) {
                        Box(
                            Modifier.size(36.dp).clip(CircleShape)
                                .background(if (voiceBusy && !transcribing) MaterialTheme.colorScheme.primary else Color.Transparent)
                                .clickable { launchVoice() },
                            contentAlignment = Alignment.Center,
                        ) {
                            if (transcribing) {
                                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                            } else {
                                Icon(
                                    Icons.Outlined.Mic, contentDescription = "语音输入",
                                    tint = if (voiceBusy) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.size(22.dp),
                                )
                            }
                        }
                    }
                    val canSend = value.isNotBlank() && (!sending || turnSteerable) && !steering
                    val interaction = remember { MutableInteractionSource() }
                    val pressed by interaction.collectIsPressedAsState()
                    val scale by animateFloatAsState(if (pressed && canSend) 0.86f else 1f, label = "sendScale")
                    Box(
                        Modifier.size(38.dp).graphicsLayer { scaleX = scale; scaleY = scale }
                            .clip(CircleShape)
                            .background(if (canSend) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant)
                            .clickable(enabled = canSend, interactionSource = interaction, indication = null, onClick = onSend),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (steering) {
                            CircularProgressIndicator(modifier = Modifier.size(17.dp), strokeWidth = 2.dp, color = Color.White)
                        } else {
                            Icon(
                                Icons.AutoMirrored.Outlined.Send,
                                contentDescription = if (sending) "追加到当前任务" else "发送",
                                tint = if (canSend) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                    if (sending) {
                        Spacer(Modifier.width(6.dp))
                        Box(
                            Modifier.size(38.dp).clip(CircleShape)
                                .background(MaterialTheme.colorScheme.onSurface)
                                .clickable(enabled = !interrupting, onClick = onStop),
                            contentAlignment = Alignment.Center,
                        ) {
                            if (interrupting) {
                                CircularProgressIndicator(modifier = Modifier.size(17.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.surface)
                            } else {
                                Box(Modifier.size(12.dp).clip(RoundedCornerShape(3.dp)).background(MaterialTheme.colorScheme.surface))
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun conversationEpoch(conv: ChatConversation): Long = listOf(
    conv.lastMessageAt, conv.updatedAt, conv.createdAt,
).firstNotNullOfOrNull { raw ->
    runCatching { java.time.Instant.parse(raw).toEpochMilli() }.getOrNull()
        ?: runCatching { java.time.OffsetDateTime.parse(raw).toInstant().toEpochMilli() }.getOrNull()
} ?: 0L

internal fun drawerConversationGroups(conversations: List<ChatConversation>): List<Pair<String, List<ChatConversation>>> {
    val now = System.currentTimeMillis()
    val day = 86_400_000L
    val orderedLabels = listOf("置顶", "今天", "昨天", "过去 7 天", "过去 30 天", "更早")
    val groups = linkedMapOf<String, MutableList<ChatConversation>>()
    conversations.sortedByDescending(::conversationEpoch).forEach { conv ->
        val age = (now - conversationEpoch(conv)).coerceAtLeast(0L)
        val label = when {
            conv.pinned -> "置顶"
            age < day -> "今天"
            age < 2 * day -> "昨天"
            age < 7 * day -> "过去 7 天"
            age < 30 * day -> "过去 30 天"
            else -> "更早"
        }
        groups.getOrPut(label) { mutableListOf() }.add(conv)
    }
    return orderedLabels.mapNotNull { label -> groups[label]?.let { label to it } }
}

private fun drawerConversationDate(conv: ChatConversation): String {
    val time = conversationEpoch(conv)
    if (time <= 0L) return ""
    val date = java.time.Instant.ofEpochMilli(time).atZone(java.time.ZoneId.systemDefault()).toLocalDate()
    return date.toString()
}

@Composable
private fun DrawerConvRow(conv: ChatConversation, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 1.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(if (selected) Accent.copy(alpha = 0.10f) else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Outlined.ChatBubbleOutline, contentDescription = null,
            tint = if (selected) Accent else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(18.dp),
        )
        Spacer(Modifier.width(11.dp))
        Column(Modifier.weight(1f)) {
            Text(
                conv.title.ifBlank { "新对话" },
                fontSize = 14.sp,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                color = if (selected) Accent else MaterialTheme.colorScheme.onSurface,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            Text(drawerConversationDate(conv), fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
