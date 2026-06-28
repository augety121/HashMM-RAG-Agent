package com.hashmm.app.ui.models
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
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
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Memory
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
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.data.remote.ModelInfo

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
            ScreenHeader(title = "模型 / 后端配置", onBack = onBack) {
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
            item { Text("客户端后端地址", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            item {
                Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp)) {
                        OutlinedTextField(
                            value = ui.backendUrl,
                            onValueChange = viewModel::onBackendChange,
                            modifier = Modifier.fillMaxWidth(),
                            placeholder = { Text("") },
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
                    Text("可用模型", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                    if (ui.loading) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    else TextButton(onClick = { viewModel.load() }) { Text("刷新", fontSize = 13.sp) }
                }
            }

            if (!ui.loading && ui.models.isEmpty()) {
                item {
                    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(14.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)), modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                            Icon(Icons.Outlined.Memory, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(34.dp))
                            Spacer(Modifier.height(10.dp))
                            Text(ui.error ?: "暂无模型，点右上角「新增」", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
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
            AddModelSheet(ui.addForm, viewModel)
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun AddModelSheet(form: AddForm, vm: ModelConfigViewModel) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp)) {
        Text("新增模型", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.height(14.dp))

        Text("厂商", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(6.dp))
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PROVIDER_PRESETS.forEach { (p, _, _) ->
                FilterChip(selected = form.provider == p, onClick = { vm.onProvider(p) }, label = { Text(p) })
            }
        }
        Spacer(Modifier.height(12.dp))

        OutlinedTextField(form.name, vm::onName, label = { Text("名称") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(form.modelName, vm::onModelName, label = { Text("模型名 model_name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(form.baseUrl, vm::onBaseUrl, label = { Text("Base URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            form.apiKey, vm::onApiKey, label = { Text("API Key（本地模型可留空）") },
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
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
        modifier = Modifier.fillMaxWidth().clickable(enabled = !m.isDefault && !switching && !deleting, onClick = onPick),
    ) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, top = 12.dp, bottom = 12.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(40.dp).clip(RoundedCornerShape(11.dp)).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.10f)),
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.Outlined.Memory, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp)) }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(m.name, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                val sub = listOf(m.provider, m.modelName).filter { it.isNotBlank() }.joinToString(" · ")
                if (sub.isNotBlank()) Text(sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            when {
                switching -> CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.primary)
                m.isDefault -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.CheckCircle, contentDescription = "当前默认", tint = Color(0xFF34C759), modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("默认", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = Color(0xFF34C759))
                }
                else -> Box(Modifier.size(20.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant))
            }
            if (deleting) CircularProgressIndicator(Modifier.size(18.dp).padding(start = 4.dp), strokeWidth = 2.dp)
            else IconButton(onClick = onDelete, enabled = !m.isDefault) {
                Icon(Icons.Outlined.Delete, contentDescription = "删除", tint = if (m.isDefault) MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
            }
        }
    }
}
