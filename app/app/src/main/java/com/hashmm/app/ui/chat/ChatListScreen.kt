package com.hashmm.app.ui.chat

import androidx.compose.foundation.background
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextAlign
import com.hashmm.app.ui.theme.Brand
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.AlertDialog
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.LockOpen
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.sync.ChatConversation

/**
 * 会话列表（微信简约风）：登录后「可见先同步」拉取的会话，立即展示。点某会话进入详情时再拉消息。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatListScreen(
    onBack: () -> Unit,
    onOpenConversation: (String) -> Unit,
    showBack: Boolean = true,
    viewModel: ChatListViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()

    // V174：本地对话搜索——纯内存按标题过滤已同步的会话（不发网络）。
    var query by remember { mutableStateOf("") }
    val filtered = if (query.isBlank()) ui.conversations
        else ui.conversations.filter { it.title.contains(query.trim(), ignoreCase = true) }

    Scaffold(
        topBar = {
            androidx.compose.material3.TopAppBar(
                title = { Text("对话") },
                navigationIcon = {
                    if (showBack) {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
                        }
                    }
                },
                actions = {
                    if (ui.syncing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(20.dp).padding(end = 12.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        IconButton(onClick = { viewModel.load() }) {
                            Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
                        }
                    }
                },
            )
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.error != null -> Column(
                    Modifier.align(Alignment.Center).padding(32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("加载失败", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(6.dp))
                    Text(ui.error!!, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(16.dp))
                    TextButton(onClick = { viewModel.load() }) { Text("重试") }
                }
                ui.conversations.isEmpty() -> Column(
                    Modifier.align(Alignment.Center).padding(40.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Box(
                        Modifier.size(76.dp).clip(CircleShape).background(Brand.copy(alpha = 0.10f)),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(
                            Icons.Outlined.ChatBubbleOutline, contentDescription = null,
                            tint = Brand, modifier = Modifier.size(36.dp),
                        )
                    }
                    Spacer(Modifier.height(18.dp))
                    Text("暂无会话", fontWeight = FontWeight.Bold, fontSize = 17.sp)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "用同一个 Supabase 账号在桌面客户端开始对话，会自动同步到这里",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                }
                else -> Column(Modifier.fillMaxSize()) {
                    val privacy by viewModel.privacy.collectAsStateWithLifecycle()
                    PrivacyBar(privacy, onToggle = { viewModel.setPrivacy(it) })
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                        placeholder = { Text("搜索对话标题") },
                        leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
                        singleLine = true,
                    )
                    if (filtered.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text(
                                "没有匹配「${query.trim()}」的对话",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    } else {
                        LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
                            items(filtered, key = { it.id }) { conv ->
                                ConversationRow(
                                    conv = conv,
                                    onClick = { onOpenConversation(conv.id) },
                                    onRename = { viewModel.renameConversation(conv.id, it) },
                                    onTogglePin = { viewModel.togglePin(conv.id) },
                                    onDelete = { viewModel.deleteConversation(conv.id) },
                                )
                                HorizontalDivider(
                                    modifier = Modifier.padding(start = 76.dp),
                                    thickness = 0.5.dp,
                                    color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.4f),
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ConversationRow(
    conv: ChatConversation,
    onClick: () -> Unit,
    onRename: (String) -> Unit = {},
    onTogglePin: () -> Unit = {},
    onDelete: () -> Unit = {},
) {
    var menu by remember { mutableStateOf(false) }
    var showRename by remember { mutableStateOf(false) }
    var showDelete by remember { mutableStateOf(false) }
    Box {
        Row(
            Modifier.fillMaxWidth()
                .combinedClickable(onClick = onClick, onLongClick = { menu = true })
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 会话图标：柔和圆形容器 + 对话符号（Material 3 tonal 风格，明暗主题自适应）；
            // 置顶会话用品牌色弱底突出。不再用高饱和红渐变首字方块（视觉噪音大、像联系人应用）。
            val pinnedBg = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
            Box(
                Modifier.size(42.dp).clip(CircleShape)
                    .background(if (conv.pinned) pinnedBg else MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Outlined.ChatBubbleOutline,
                    contentDescription = null,
                    modifier = Modifier.size(20.dp),
                    tint = if (conv.pinned) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (conv.pinned) {
                        Icon(
                            Icons.Outlined.PushPin,
                            contentDescription = "置顶",
                            modifier = Modifier.size(14.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.width(4.dp))
                    }
                    Text(
                        conv.title.ifBlank { "新对话" },
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Spacer(Modifier.width(8.dp))
            Text(
                formatChatTime(ChatMessageOps.conversationActivityTimestamp(conv)),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(
                text = { Text(if (conv.pinned) "取消置顶" else "置顶") },
                leadingIcon = { Icon(Icons.Outlined.PushPin, null, Modifier.size(18.dp)) },
                onClick = { menu = false; onTogglePin() },
            )
            DropdownMenuItem(
                text = { Text("重命名") },
                leadingIcon = { Icon(Icons.Outlined.Edit, null, Modifier.size(18.dp)) },
                onClick = { menu = false; showRename = true },
            )
            DropdownMenuItem(
                text = { Text("删除", color = MaterialTheme.colorScheme.error) },
                leadingIcon = { Icon(Icons.Outlined.Delete, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.error) },
                onClick = { menu = false; showDelete = true },
            )
        }
    }
    if (showRename) {
        var renameText by remember { mutableStateOf(conv.title) }
        AlertDialog(
            onDismissRequest = { showRename = false },
            title = { Text("重命名会话") },
            text = {
                OutlinedTextField(
                    value = renameText,
                    onValueChange = { renameText = it },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    val t = renameText.trim()
                    if (t.isNotBlank()) onRename(t)
                    showRename = false
                }) { Text("保存") }
            },
            dismissButton = { TextButton(onClick = { showRename = false }) { Text("取消") } },
        )
    }
    if (showDelete) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            title = { Text("删除会话") },
            text = { Text("确定删除「${conv.title.ifBlank { "新对话" }}」？会删掉该会话的消息与文件，不可恢复。") },
            confirmButton = {
                TextButton(onClick = { showDelete = false; onDelete() }) { Text("删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { showDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun PrivacyBar(state: Triple<Boolean, Boolean, Boolean>?, onToggle: (Boolean) -> Unit) {
    val on = state?.first == true
    val llmLocal = state?.second == true
    val localAvailable = state?.third == true
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            if (on) Icons.Outlined.Lock else Icons.Outlined.LockOpen, contentDescription = null,
            modifier = Modifier.size(18.dp),
            tint = if (on) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.width(8.dp))
        Column(Modifier.weight(1f)) {
            Text("本地隐私模式", fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Text(
                when {
                    on && llmLocal -> "对话不上云 · LLM 也在本机，完全本地 ✓"
                    on && localAvailable -> "对话不上云 · 正在切到本地模型…"
                    on -> "对话不上云 · 但没配本地模型，LLM 仍走云端（去后台加一个指向本机的模型）"
                    else -> "关闭：对话会同步到云端，跨端可用"
                },
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(checked = on, onCheckedChange = onToggle)
    }
}
