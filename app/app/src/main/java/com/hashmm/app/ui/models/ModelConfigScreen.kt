package com.hashmm.app.ui.models
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.remote.ModelInfo
import com.hashmm.app.data.remote.ModelProviderInfo

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ModelConfigScreen(
    onBack: () -> Unit,
    viewModel: ModelConfigViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(ui.toast) { ui.toast?.let { snackbar.showSnackbar(it); viewModel.clearToast() } }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            ScreenHeader(title = "我的 API 与模型", subtitle = "仅当前账号可用；密钥不会展示给其他用户", onBack = onBack) {
                TextButton(onClick = { viewModel.openAdd() }) { Icon(Icons.Outlined.Add, contentDescription = null, modifier = Modifier.size(18.dp)); Spacer(Modifier.width(2.dp)); Text("新增") }
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { Text("客户端后端地址", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            item {
                // V244：去描边（无边白卡）
                Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp)) {
                        OutlinedTextField(
                            value = ui.backendUrl,
                            onValueChange = viewModel::onBackendChange,
                            modifier = Modifier.fillMaxWidth(),
                            placeholder = { Text("https://hashmm.hashlens.org") },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                            keyboardActions = KeyboardActions(onDone = { viewModel.saveBackend() }),
                        )
                        Spacer(Modifier.height(10.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Button(onClick = { viewModel.saveBackend() }, shape = RoundedCornerShape(10.dp)) { Text("保存并连接") }
                            if (ui.backendSaved) { Spacer(Modifier.width(10.dp)); Text("已保存", fontSize = 13.sp, color = Color(0xFF34C759)) }
                        }
                    }
                }
            }

            item { Spacer(Modifier.height(4.dp)) }
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("可用模型", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                    if (ui.loading) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    else TextButton(onClick = { viewModel.load() }) { Text("刷新", fontSize = 13.sp) }
                }
            }

            if (!ui.loading && ui.models.isEmpty()) {
                item {
                    com.hashmm.app.ui.components.HmmStateView(
                        kind = com.hashmm.app.ui.components.HmmStateKind.Empty,
                        icon = Icons.Outlined.Memory,
                        title = "暂无模型",
                        message = ui.error ?: "点右上角「新增」添加一个模型配置",
                    )
                }
            }

            items(ui.models, key = { it.id }) { m ->
                ModelCard(
                    m,
                    switching = ui.switching == m.id,
                    deleting = ui.deleting == m.id,
                    onPick = { viewModel.setDefault(m.id) },
                    onDelete = { viewModel.deleteModel(m.id) },
                )
            }
        }
    }

    // 新增模型底部表单
    if (ui.addForm.show) {
        ModalBottomSheet(onDismissRequest = { viewModel.closeAdd() }) {
            AddModelSheet(ui.addForm, ui.providers, ui.providerError, viewModel)
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun AddModelSheet(
    form: AddForm,
    providers: List<ModelProviderInfo>,
    providerError: String?,
    vm: ModelConfigViewModel,
) {
    var providerMenu by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp)) {
        Text("新增模型", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
        Text("连接策略与桌面端共用；模型 ID 以你的服务商账号为准", fontSize = 11.5.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(14.dp))

        Box(Modifier.fillMaxWidth()) {
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(12.dp))
                    .clickable { providerMenu = true },
            ) {
                Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("服务商", fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(form.providerName, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    }
                    Icon(Icons.Outlined.KeyboardArrowDown, contentDescription = "选择服务商")
                }
            }
            DropdownMenu(expanded = providerMenu, onDismissRequest = { providerMenu = false }) {
                providers.forEach { provider ->
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(provider.name, fontSize = 13.sp)
                                Text(provider.id, fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        },
                        onClick = { vm.onProvider(provider.id); providerMenu = false },
                    )
                }
            }
        }
        if (!providerError.isNullOrBlank()) {
            Spacer(Modifier.height(6.dp))
            Text("正在使用内置兼容清单；连接服务器后会同步完整厂商策略。", fontSize = 10.5.sp,
                color = MaterialTheme.colorScheme.tertiary)
        }
        Spacer(Modifier.height(12.dp))

        if (form.wireApis.size > 1) {
            Text("接口协议", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                form.wireApis.forEach { wire ->
                    FilterChip(
                        selected = form.wireApi == wire,
                        onClick = { vm.onWireApi(wire) },
                        label = { Text(when (wire) {
                            "responses" -> "Responses"
                            "anthropic_messages" -> "Anthropic Messages"
                            else -> "Chat Completions"
                        }) },
                    )
                }
            }
            Spacer(Modifier.height(12.dp))
        }

        if (form.endpointNote.isNotBlank()) {
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth()) {
                Text(form.endpointNote, fontSize = 11.5.sp, lineHeight = 16.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(12.dp))
            }
            Spacer(Modifier.height(12.dp))
        }

        OutlinedTextField(form.name, vm::onName, label = { Text("名称") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(form.modelName, vm::onModelName, label = { Text("模型或部署 ID") },
            placeholder = { Text("从服务商控制台复制") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        if (form.modelHints.isNotEmpty()) {
            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                form.modelHints.forEach { hint -> AssistChip(onClick = { vm.onModelName(hint) }, label = { Text(hint) }) }
            }
        }
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(form.baseUrl, vm::onBaseUrl, label = { Text("Base URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            form.apiKey, vm::onApiKey, label = { Text(if (form.apiKeyOptional) "API Key（可选）" else "API Key") },
            singleLine = true, visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done), modifier = Modifier.fillMaxWidth(),
        )

        form.testMsg?.let { msg ->
            Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    if (form.testOk == true) Icons.Outlined.CheckCircle else Icons.Outlined.ErrorOutline,
                    contentDescription = null,
                    tint = if (form.testOk == true) Color(0xFF34C759) else MaterialTheme.colorScheme.error,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(6.dp))
                Text(msg, fontSize = 12.sp, color = if (form.testOk == true) Color(0xFF1E8E3E) else MaterialTheme.colorScheme.error)
            }
        }

        Spacer(Modifier.height(16.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onClick = { vm.testAdd() }, enabled = form.canSubmit && !form.testing, modifier = Modifier.weight(1f)) {
                if (form.testing) { CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp); Spacer(Modifier.width(6.dp)); Text("测试中") }
                else Text("测试连接")
            }
            Button(onClick = { vm.saveAdd() }, enabled = form.canSubmit && !form.saving, modifier = Modifier.weight(1f)) {
                if (form.saving) { CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary); Spacer(Modifier.width(6.dp)); Text("保存中") }
                else Text("保存")
            }
        }
    }
}

@Composable
private fun ModelCard(m: ModelInfo, switching: Boolean, deleting: Boolean, onPick: () -> Unit, onDelete: () -> Unit) {
    // V244：去描边（无边白卡）；图标裸放墨黑（去淡染图标盒）；未选中改细圆环（不再是灰实心）
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).clickable(enabled = !m.isDefault && !switching && !deleting, onClick = onPick),
    ) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, top = 13.dp, bottom = 13.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Memory, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(21.dp))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(m.name, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                val sub = listOf(m.provider, m.modelName, m.wireApi).filter { it.isNotBlank() }.joinToString(" · ")
                if (sub.isNotBlank()) Text(sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            when {
                switching -> CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.primary)
                m.isDefault -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.CheckCircle, contentDescription = "当前默认", tint = Color(0xFF34C759), modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("默认", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = Color(0xFF34C759))
                }
                else -> Box(Modifier.size(19.dp).clip(CircleShape)
                    .border(1.5.dp, MaterialTheme.colorScheme.outlineVariant, CircleShape))
            }
            if (deleting) CircularProgressIndicator(Modifier.size(18.dp).padding(start = 4.dp), strokeWidth = 2.dp)
            else IconButton(onClick = onDelete, enabled = !m.isDefault) {
                Icon(Icons.Outlined.Delete, contentDescription = "删除", tint = if (m.isDefault) MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
            }
        }
    }
}
