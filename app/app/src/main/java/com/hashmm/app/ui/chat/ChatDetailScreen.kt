package com.hashmm.app.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Send
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.ui.components.HashMascot

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatDetailScreen(
    onBack: () -> Unit,
    viewModel: ChatDetailViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val viewerUrl by viewModel.viewerUrl.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    LaunchedEffect(ui.messages.size) {
        if (ui.messages.isNotEmpty()) listState.animateScrollToItem(ui.messages.size - 1)
    }

    androidx.compose.runtime.CompositionLocalProvider(
        LocalFileOpener provides { filename, downloadUrl -> viewModel.openFile(downloadUrl, filename) }
    ) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    val generating = ui.sending || ui.messages.any { it.status == "streaming" }
                    Text(if (generating) "对话 · 生成中…" else "对话", fontWeight = FontWeight.Bold)
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
        bottomBar = { ChatInput(sending = ui.sending, onSend = { viewModel.send(it) }) },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.messages.isEmpty() -> Column(
                    Modifier.align(Alignment.Center).padding(32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(
                        if (ui.error != null) "加载失败" else "继续这个会话",
                        style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface,
                    )
                    Spacer(Modifier.height(6.dp))
                    Text(
                        ui.error ?: "下面输入就能接着和 Agent 聊（和客户端同一个会话）。",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    state = listState,
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 10.dp),
                ) {
                    items(ui.messages, key = { it.id }) { msg -> MessageBubble(msg) }
                }
            }
            if (ui.error != null && ui.messages.isNotEmpty()) {
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp),
                ) {
                    Text(ui.error!!, color = MaterialTheme.colorScheme.onErrorContainer, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                }
            }
        }
    }
    viewerUrl?.let { InAppFileViewer(url = it, onClose = { viewModel.closeViewer() }) }
    }
}

@Composable
private fun ChatInput(sending: Boolean, onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    val haptic = androidx.compose.ui.platform.LocalHapticFeedback.current
    Surface(color = MaterialTheme.colorScheme.surface) {
        Row(
            Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(24.dp),
                modifier = Modifier.weight(1f),
            ) {
                BasicTextField(
                    value = text,
                    onValueChange = { text = it },
                    enabled = !sending,
                    textStyle = TextStyle(color = MaterialTheme.colorScheme.onSurface, fontSize = 15.sp),
                    cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                    decorationBox = { inner ->
                        if (text.isEmpty()) Text(if (sending) "Agent 正在回复…" else "继续这个会话…", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 15.sp)
                        inner()
                    },
                )
            }
            Spacer(Modifier.size(8.dp))
            val active = text.isNotBlank() && !sending
            Box(
                Modifier.size(44.dp).clip(CircleShape)
                    .background(if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center,
            ) {
                if (sending) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    IconButton(onClick = { if (active) { haptic.performHapticFeedback(androidx.compose.ui.hapticfeedback.HapticFeedbackType.LongPress); onSend(text); text = "" } }, enabled = active) {
                        Icon(Icons.Outlined.Send, contentDescription = "发送", tint = if (active) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

@Composable
private fun ChatInputPlaceholder() {}

