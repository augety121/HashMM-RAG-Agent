package com.hashmm.app.ui.knowledge
import com.hashmm.app.ui.components.ScreenHeader

import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.UploadFile
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.remote.CorpusStats
import com.hashmm.app.data.remote.KbDoc
import com.hashmm.app.ui.components.HashMascot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun KnowledgeScreen(onBack: () -> Unit, viewModel: KnowledgeViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    // 选文件 → 读字节 → 上传
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            scope.launch(Dispatchers.IO) {
                val cr = context.contentResolver
                var name = "document"
                runCatching {
                    cr.query(uri, null, null, null, null)?.use { c ->
                        val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        if (idx >= 0 && c.moveToFirst()) c.getString(idx)?.let { name = it }
                    }
                }
                val mime = cr.getType(uri) ?: "application/octet-stream"
                val bytes = runCatching { cr.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
                if (bytes != null) viewModel.upload(name, bytes, mime)
                else viewModel.upload(name, ByteArray(0), mime) // 触发"文件为空"提示
            }
        }
    }

    // 上传结果用 Snackbar 提示
    LaunchedEffect(ui.uploadMsg) {
        ui.uploadMsg?.let { snackbar.showSnackbar(it); viewModel.clearUploadMsg() }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { if (!ui.uploading) picker.launch("*/*") },
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            ) {
                if (ui.uploading) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                    Spacer(Modifier.width(8.dp)); Text("上传中…")
                } else {
                    Icon(Icons.Outlined.UploadFile, contentDescription = null, modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp)); Text("上传文档")
                }
            }
        },
        topBar = {
            ScreenHeader(title = "知识库", onBack = onBack) {
                IconButton(onClick = { viewModel.load() }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
                    }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                ui.loading && ui.stats == null -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.stats == null -> EmptyKnowledge(ui.error, onRetry = { viewModel.load() })
                else -> LazyColumn(
                    Modifier.fillMaxSize().padding(horizontal = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    item { Spacer(Modifier.height(4.dp)); StatsCard(ui.stats!!) }
                    item {
                        Text(
                            "已索引文档 ${ui.documents.size}",
                            fontWeight = FontWeight.SemiBold, fontSize = 15.sp,
                            color = MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.padding(start = 2.dp, top = 6.dp),
                        )
                    }
                    if (ui.documents.isEmpty()) {
                        item {
                            Text(
                                "暂无文档明细（后端未暴露列表或语料由切片直接导入）",
                                fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(start = 2.dp, bottom = 8.dp),
                            )
                        }
                    } else {
                        items(ui.documents) { d -> DocCard(d) }
                    }
                    item { Spacer(Modifier.height(12.dp)) }
                }
            }
        }
    }
}

@Composable
private fun StatsCard(s: CorpusStats) {
    Surface(
        color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(18.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.fillMaxWidth().padding(18.dp)) {
            Text("知识切片", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(2.dp))
            Text(
                "${s.totalChunks}",
                fontSize = 34.sp, fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Spacer(Modifier.height(14.dp))
            Row(Modifier.fillMaxWidth()) {
                StatCell("索引大小", if (s.indexSizeKb > 1024) "${s.indexSizeKb / 1024} MB" else "${s.indexSizeKb} KB", Modifier.weight(1f))
                StatCell("哈希位宽", if (s.hashBits > 0) "${s.hashBits}" else "—", Modifier.weight(1f))
                StatCell("模型", if (s.llmReady) "就绪" else "未就绪", Modifier.weight(1f))
            }
            if (s.activeModel.isNotBlank()) {
                Spacer(Modifier.height(12.dp))
                Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(10.dp)) {
                    Text(
                        s.activeModel, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    )
                }
            }
            if (s.modalities.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    s.modalities.entries.take(4).forEach { (k, v) ->
                        Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(8.dp)) {
                            Text(
                                "$k · $v", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatCell(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(value, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.height(2.dp))
        Text(label, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun DocCard(d: KbDoc) {
    Surface(
        color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(38.dp).clip(RoundedCornerShape(10.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center,
            ) {
                Icon(Icons.Outlined.Description, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(d.filename, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface, maxLines = 1)
                Spacer(Modifier.height(3.dp))
                Text(
                    "${d.chunks} 切片" + if (d.modalities.isNotEmpty()) " · " + d.modalities.joinToString("/") else "",
                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun EmptyKnowledge(error: String?, onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        HashMascot(Modifier.size(92.dp))
        Spacer(Modifier.height(18.dp))
        Text("知识库暂不可用", fontSize = 17.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.height(8.dp))
        Text(
            error ?: "请确认客户端后端在线，并在「我的」里填好客户端地址。",
            fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(20.dp))
        OutlinedButton(onClick = onRetry) { Text("重试") }
    }
}
