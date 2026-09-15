package com.hashmm.app.ui.profile

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hashmm.app.BuildConfig
import com.hashmm.app.data.remote.ClientStatusRepository
import com.hashmm.app.data.settings.SettingsStore
import com.hashmm.app.ui.components.HashMascot
import com.hashmm.app.ui.components.InfoDivider
import com.hashmm.app.ui.components.InfoGroup
import com.hashmm.app.ui.components.InfoIntroCard
import com.hashmm.app.ui.components.InfoSectionLabel
import com.hashmm.app.ui.components.ScreenHeader
import com.hashmm.app.ui.settings.SettingsViewModel
import com.hashmm.app.ui.theme.AvatarGradient
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

// ═══════════════════════════════ 客户端连接（重设计的独立页面） ═══════════════════════════════

data class ClientConnUi(
    val checking: Boolean = true,
    val online: Boolean = false,
    val latencyMs: Long = -1,
    val failureClass: String? = null,
)

@HiltViewModel
class ClientConnViewModel @Inject constructor(
    private val settings: SettingsStore,
    private val status: ClientStatusRepository,
) : ViewModel() {
    val clientUrl: StateFlow<String> = settings.clientUrl.stateIn(viewModelScope, SharingStarted.Eagerly, "")
    val isAutoConfigured: StateFlow<Boolean> = settings.isAutoConfigured.stateIn(viewModelScope, SharingStarted.Eagerly, false)
    val source: StateFlow<String> = settings.clientUrlSource.stateIn(viewModelScope, SharingStarted.Eagerly, "compiled_default")

    private val _ui = MutableStateFlow(ClientConnUi())
    val ui: StateFlow<ClientConnUi> = _ui.asStateFlow()

    init { test() }

    /** 测一次连接：记录在线与耗时。 */
    fun test() {
        _ui.value = _ui.value.copy(checking = true)
        viewModelScope.launch {
            val t0 = System.currentTimeMillis()
            val s = runCatching { status.fetch() }.getOrNull()
            val dt = System.currentTimeMillis() - t0
            _ui.value = ClientConnUi(
                checking = false,
                online = s?.online == true,
                latencyMs = if (s?.online == true) dt else -1,
                failureClass = s?.failureClass,
            )
        }
    }

    fun save(url: String, onDone: () -> Unit) {
        viewModelScope.launch { settings.setClientUrl(url.trim()); test(); onDone() }
    }

    fun restoreAutomatic(onDone: () -> Unit) {
        viewModelScope.launch { settings.clearManualClientUrl(); test(); onDone() }
    }
}

/** 客户端连接：状态卡（在线/离线/耗时）+ 工作原理说明 + 地址配置 + 测试连接。 */
@Composable
fun ClientConnectionScreen(onBack: () -> Unit, viewModel: ClientConnViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val url by viewModel.clientUrl.collectAsStateWithLifecycle()
    val auto by viewModel.isAutoConfigured.collectAsStateWithLifecycle()
    val source by viewModel.source.collectAsStateWithLifecycle()
    var input by remember(url) { mutableStateOf(url) }
    var saved by remember { mutableStateOf(false) }

    Scaffold(topBar = { ScreenHeader("客户端连接", onBack) }, containerColor = MaterialTheme.colorScheme.background) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        ) {
            Spacer(Modifier.height(6.dp))
            // 状态卡
            Card(
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
            ) {
                Row(Modifier.fillMaxWidth().padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                    when {
                        ui.checking -> CircularProgressIndicator(Modifier.size(26.dp), strokeWidth = 2.5.dp)
                        ui.online -> Icon(Icons.Outlined.CheckCircle, contentDescription = null, tint = Color(0xFF2E7D32), modifier = Modifier.size(30.dp))
                        else -> Icon(Icons.Outlined.ErrorOutline, contentDescription = null, tint = MaterialTheme.colorScheme.error, modifier = Modifier.size(30.dp))
                    }
                    Spacer(Modifier.width(14.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            when { ui.checking -> "正在检测…"; ui.online -> "已连接"; else -> "未连接" },
                            fontSize = 17.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface,
                        )
                        Spacer(Modifier.height(3.dp))
                        Text(
                            when {
                                ui.checking -> "正在探测客户端后端"
                                ui.online -> "响应 ${ui.latencyMs} ms" + if (auto) " · 地址已自动同步" else ""
                                url.isBlank() -> "尚未配置地址；电脑端登录同账号后会自动同步"
                                ui.failureClass == "identity_mismatch" -> "服务器可达，但账号项目或用户身份不一致"
                                ui.failureClass == "authentication_expired" -> "登录状态已过期，请重新登录"
                                ui.failureClass == "bootstrap_failed" -> "服务器可达，但身份启动协议未通过"
                                else -> "地址不可达：确认 HashMM 后端和隧道在线"
                            },
                            fontSize = 12.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 17.sp,
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                    OutlinedButton(onClick = { viewModel.test() }, enabled = !ui.checking, shape = RoundedCornerShape(10.dp)) {
                        Text("重测", fontSize = 13.sp)
                    }
                }
            }

            Spacer(Modifier.height(14.dp))
            // 工作原理
            Card(
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
            ) {
                Column(Modifier.padding(18.dp)) {
                    Text("它是怎么连上的", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(8.dp))
                    ConnStep("1", "电脑端登录同一账号并启动客户端")
                    ConnStep("2", "客户端把自己的公网地址同步到云端")
                    ConnStep("3", "App 自动取到地址——知识库、取文件、电脑操作都经它直达你的电脑")
                    Spacer(Modifier.height(6.dp))
                    Text("整个过程零配置；只有你自己的账号能拿到这个地址。", fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }

            Spacer(Modifier.height(14.dp))
            // 手动地址（高级）
            Card(
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
            ) {
                Column(Modifier.padding(18.dp)) {
                    Text("手动指定地址（高级）", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(4.dp))
                    Text("一般无需手填。填写后以手动地址优先。", fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(
                        "当前来源：" + when (source) { "manual" -> "手动覆盖"; "cloud" -> "云端自动配置"; else -> "安装包默认" },
                        fontSize = 11.5.sp,
                        color = if (source == "manual") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(
                        value = input,
                        onValueChange = { input = it; saved = false },
                        placeholder = { Text("https://your-tunnel.example.com") },
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = { viewModel.save(input) { saved = true } },
                        enabled = input.isNotBlank() && !ui.checking,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().height(46.dp),
                    ) { Text(if (saved) "已保存并重测" else "保存并测试") }
                    if (source == "manual") {
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(
                            onClick = { viewModel.restoreAutomatic { input = url; saved = false } },
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth().height(46.dp),
                        ) { Text("恢复自动配置") }
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun ConnStep(no: String, text: String) {
    Row(Modifier.padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(20.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)), contentAlignment = Alignment.Center) {
            Text(no, fontSize = 11.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
        }
        Spacer(Modifier.width(10.dp))
        Text(text, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface, lineHeight = 18.sp)
    }
}

// ═══════════════════════════════ 个人信息 ═══════════════════════════════

/** 个人信息：头像（点按更换）、昵称（与桌面端互通）、账号邮箱、用户 ID（可复制）。 */
@Composable
fun PersonalInfoScreen(onBack: () -> Unit, viewModel: SettingsViewModel = hiltViewModel()) {
    val avatar by viewModel.avatar.collectAsStateWithLifecycle()
    val displayName by viewModel.displayName.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val email = viewModel.email().orEmpty()
    val uid = viewModel.uid().orEmpty()
    var nameInput by remember(displayName) { mutableStateOf(displayName) }
    var nameSaved by remember { mutableStateOf(false) }
    var nameFailed by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { viewModel.refreshDisplayName() }
    val avatarPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            val bytes = runCatching { context.contentResolver.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
            if (bytes != null && bytes.isNotEmpty()) viewModel.uploadAvatar(bytes) {}
        }
    }

    Scaffold(topBar = { ScreenHeader("个人信息", onBack) }, containerColor = MaterialTheme.colorScheme.background) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        ) {
            Spacer(Modifier.height(10.dp))
            // 头像
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(contentAlignment = Alignment.BottomEnd) {
                    Box(
                        Modifier.size(88.dp).clip(RoundedCornerShape(30.dp)).background(AvatarGradient)
                            .clickable { avatarPicker.launch("image/*") },
                        contentAlignment = Alignment.Center,
                    ) {
                        val a = avatar
                        if (a != null) Image(bitmap = a, contentDescription = "头像", contentScale = ContentScale.Crop, modifier = Modifier.size(88.dp).clip(RoundedCornerShape(30.dp)))
                        else Text((email.substringBefore("@").firstOrNull()?.uppercaseChar() ?: '#').toString(), color = Color.White, fontWeight = FontWeight.ExtraBold, fontSize = 36.sp)
                    }
                    Box(
                        Modifier.size(26.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary),
                        contentAlignment = Alignment.Center,
                    ) { Icon(Icons.Outlined.PhotoCamera, contentDescription = "更换头像", tint = Color.White, modifier = Modifier.size(14.dp)) }
                }
                Spacer(Modifier.height(8.dp))
                Text("点击更换头像", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            Spacer(Modifier.height(20.dp))
            // 昵称
            Card(
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
            ) {
                Column(Modifier.padding(18.dp)) {
                    Text("昵称", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(4.dp))
                    Text("与电脑客户端同步显示", fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(
                        value = nameInput,
                        onValueChange = { nameInput = it; nameSaved = false; nameFailed = false },
                        placeholder = { Text(email.substringBefore("@").ifBlank { "你的昵称" }) },
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = { viewModel.saveDisplayName(nameInput) { ok -> nameSaved = ok; nameFailed = !ok } },
                        enabled = nameInput.trim().isNotBlank() && nameInput.trim() != displayName,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().height(46.dp),
                    ) { Text(if (nameSaved) "已保存" else "保存昵称") }
                    if (nameFailed) {
                        Spacer(Modifier.height(8.dp))
                        Text("保存失败：请检查网络后重试", fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
                    }
                }
            }

            Spacer(Modifier.height(14.dp))
            // 账号信息（只读）
            Card(
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
            ) {
                Column {
                    InfoRow("登录邮箱", email.ifBlank { "未登录" })
                    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                    Row(
                        Modifier.fillMaxWidth().clickable(enabled = uid.isNotBlank()) {
                            val clipboard = context.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
                            clipboard.setPrimaryClip(android.content.ClipData.newPlainText("HashMM user ID", uid))
                        }
                            .padding(horizontal = 18.dp, vertical = 15.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("用户 ID", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurface)
                            Spacer(Modifier.height(2.dp))
                            Text(if (uid.isBlank()) "-" else uid, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                        }
                        Icon(Icons.Outlined.ContentCopy, contentDescription = "复制", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(17.dp))
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "头像与昵称保存在你的账号档案里，仅用于在各端展示；删除账号数据可联系「帮助与反馈」。",
                fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 16.sp,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 15.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
        Text(
            value,
            fontSize = 12.5.sp,
            lineHeight = 17.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.End,
            maxLines = 3,
            modifier = Modifier.fillMaxWidth(0.62f),
        )
    }
}

// ═══════════════════════════════ 关于 HashMM ═══════════════════════════════

@Composable
fun AboutScreen(onBack: () -> Unit, onOpenTerms: () -> Unit, onOpenPrivacy: () -> Unit) {
    Scaffold(topBar = { ScreenHeader("关于 HashMM", onBack) }, containerColor = MaterialTheme.colorScheme.background) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(20.dp))
            HashMascot(Modifier.size(96.dp))
            Spacer(Modifier.height(12.dp))
            Text("HashMM", fontSize = 24.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(4.dp))
            Text("版本 ${BuildConfig.VERSION_NAME}", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(18.dp))
            InfoIntroCard(
                title = "面向长对话与长任务的 RAG-Agent 工作空间",
                body = "HashMM 把对话作为统一入口：检索、记忆、多智能体、浏览器、电脑操作和文档产物都回到同一会话中。手机负责随时发起、审批与接管，桌面端和自建后端负责需要本地资源或持续运行的任务。",
            )
            Spacer(Modifier.height(12.dp))
            InfoSectionLabel("产品信息", modifier = Modifier.align(Alignment.Start))
            InfoGroup {
                InfoRow("当前版本", BuildConfig.VERSION_NAME)
                InfoDivider(start = 18.dp)
                InfoRow("数据归属", "由部署者和当前账号持有人控制；分别存于自建后端、本机与自有 Supabase 项目")
                InfoDivider(start = 18.dp)
                InfoRow("产品形态", "自部署、跨端协同的 RAG-Agent 工作空间；不是托管式公共聊天服务")
            }
            Spacer(Modifier.height(12.dp))
            InfoSectionLabel("法律信息", modifier = Modifier.align(Alignment.Start))
            InfoGroup {
                AboutLink("软件许可及服务协议", onOpenTerms)
                InfoDivider(start = 18.dp)
                AboutLink("隐私政策", onOpenPrivacy)
            }
            Spacer(Modifier.height(18.dp))
            Text("HashMM Team", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun AboutLink(title: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 18.dp, vertical = 15.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
        Icon(
            Icons.AutoMirrored.Filled.KeyboardArrowRight,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(18.dp),
        )
    }
}

// ═══════════════════════════════ 帮助与反馈 ═══════════════════════════════

@Composable
fun HelpFeedbackScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    Scaffold(topBar = { ScreenHeader("帮助与反馈", onBack) }, containerColor = MaterialTheme.colorScheme.background) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        ) {
            Spacer(Modifier.height(6.dp))
            InfoIntroCard(
                title = "先确认连接，再检查任务状态",
                body = "多数问题都能在「我的 → 客户端连接」和「动态 → 进行中」中定位。下面按常见场景给出可执行的排查步骤。",
            )
            Spacer(Modifier.height(12.dp))
            InfoSectionLabel("常见问题")
            InfoGroup {
            FaqRow(
                "手机连不上电脑怎么办？",
                "1. 确认桌面客户端已启动并登录同一账号；2. 在「我的 → 客户端连接」中重测；3. 校园网或公司网可在远程控制中使用中继模式。",
            )
            InfoDivider(start = 16.dp)
            FaqRow(
                "语音输入说没有语音服务？",
                "已内置三级方案：本机识别 → 录音传电脑端转写 → 系统识别弹窗。只要电脑客户端在线，任何手机都能用语音。",
            )
            InfoDivider(start = 16.dp)
            FaqRow(
                "「电脑任务」和普通对话有什么区别？",
                "电脑任务由你的电脑客户端执行（取文件、操作电脑、浏览器查询），普通对话直接问 Agent。工作台里两组分开列出。",
            )
            InfoDivider(start = 16.dp)
            FaqRow(
                "接力发出去没反应？",
                "接力页会显示实时状态：已送达后等待桌面端接管（约几秒）；桌面端离线时先启动电脑客户端再试。",
            )
            }
            Spacer(Modifier.height(12.dp))
            InfoSectionLabel("反馈问题")
            InfoGroup {
                Row(
                    Modifier.fillMaxWidth().clickable {
                        try {
                            val intent = android.content.Intent(android.content.Intent.ACTION_SENDTO).apply {
                                data = android.net.Uri.parse("mailto:feedback@hashmm.app")
                                putExtra(android.content.Intent.EXTRA_SUBJECT, "HashMM App 反馈（v${BuildConfig.VERSION_NAME}）")
                                putExtra(
                                    android.content.Intent.EXTRA_TEXT,
                                    "问题描述：\n\n复现步骤：\n\n——\n版本 ${BuildConfig.VERSION_NAME} · Android ${android.os.Build.VERSION.RELEASE} · ${android.os.Build.MODEL}",
                                )
                            }
                            context.startActivity(intent)
                        } catch (_: Exception) {
                            android.widget.Toast.makeText(context, "未找到邮件应用，可发送至 feedback@hashmm.app", android.widget.Toast.LENGTH_LONG).show()
                        }
                    }.padding(18.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Outlined.Mail, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp))
                    Spacer(Modifier.width(13.dp))
                    Column(Modifier.weight(1f)) {
                        Text("邮件反馈", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(2.dp))
                        Text("自动带上版本与机型信息，方便定位问题", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun FaqRow(q: String, a: String) {
    var open by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().clickable { open = !open }.padding(horizontal = 16.dp, vertical = 14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(q, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
            Text(if (open) "收起" else "展开", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (open) {
            Spacer(Modifier.height(8.dp))
            Text(a, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 20.sp)
        }
    }
}

// ═══════════════════════════════ 个人信息采集清单 ═══════════════════════════════

private data class CollectItem(
    val name: String,
    val purpose: String,
    val scene: String,
    val where: String,
    val control: String,
)

/** 个人信息采集清单：如实列出 App 采集/使用的信息、目的、场景、存放位置。 */
@Composable
fun DataCollectionScreen(onBack: () -> Unit) {
    val items = listOf(
        CollectItem("账号标识", "登录鉴权、RLS 隔离和跨设备同步", "注册、登录和刷新会话时", "你的 Supabase Auth 与本机登录状态", "可退出登录；账号删除由你的 Supabase 管理员执行"),
        CollectItem("头像与昵称（可选）", "在手机和桌面端展示个人资料", "你主动设置或更换时", "自有 Supabase 项目的 profiles 表", "可在个人信息页修改或清除"),
        CollectItem("对话、消息与反馈", "连续问答、恢复历史、跨端接力和质量改进", "发送消息、点赞或点踩时", "自建后端；启用同步时镜像到自有 Supabase 项目", "可逐个删除会话；服务器与 Supabase 数据由部署者管理"),
        CollectItem("知识库文档、切片与记忆", "RAG 检索、引用溯源和个性化召回", "主动上传、索引或保存记忆时", "自建后端的数据库、索引和文件目录；部分记忆可同步到 Supabase", "可在知识库、记忆中心或服务器侧删除"),
        CollectItem("任务指令、计划、工具轨迹与产物", "执行浏览器、Computer Use、多 Agent 和长任务", "你发起电脑任务或 Agent 调用工具时", "自建后端、桌面端工作区和会话产物目录", "高风险操作应经过审批；可取消任务并删除会话产物"),
        CollectItem("模型请求上下文", "生成回答、总结、检索改写和工具规划", "调用你配置的云端模型时", "发送给你选择的模型 API 服务商；本地模型不外发", "可切换本地模型、隐私模式或更换服务商"),
        CollectItem("麦克风录音", "把语音转换为文字", "你按住或点击语音按钮并授权后", "先写入 App 临时缓存；需要后端转写时发送到自建后端，完成后删除临时文件", "可拒绝麦克风权限并始终使用文字输入"),
        CollectItem("相册图片与文件（可选）", "设置头像、发送附件或响应电脑端取图请求", "仅在你主动选择，或明确同意电脑端请求后", "仅处理所选文件；按功能发送到自建后端或自有 Supabase 项目", "系统权限可随时撤销；电脑端取图每次都需确认"),
        CollectItem("剪贴板内容（主动操作）", "在远程控制时把指定文本发送到自己的电脑", "你点击剪贴板发送按钮时", "经自建后端或设备连接传给你的桌面端", "不会后台持续读取；不点击就不处理"),
        CollectItem("连接、错误和工具审计信息", "诊断断连、追踪任务状态和审计高风险操作", "连接后端、执行工具或发生错误时", "App 本机日志与自建后端审计记录", "部署者可配置保留周期并清理日志"),
        CollectItem("设备型号与系统版本", "在反馈时提供可复现环境", "你主动创建反馈邮件时", "只写入待发送邮件正文，由你确认后发送", "发送前可编辑或删除这些信息"),
    )
    Scaffold(topBar = { ScreenHeader("个人信息采集清单", onBack) }, containerColor = MaterialTheme.colorScheme.background) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        ) {
            Spacer(Modifier.height(4.dp))
            InfoIntroCard(
                title = "最小必要原则",
                body = "清单按真实数据路径区分 App 本机、自建后端、自有 Supabase 项目、桌面端和模型服务商。是否外发取决于你启用的功能与所选模型，不把“自部署”误写成“任何情况下都不联网”。",
            )
            Spacer(Modifier.height(12.dp))
            InfoSectionLabel("采集清单", hint = "共 ${items.size} 项")
            InfoGroup {
                items.forEachIndexed { index, item ->
                    if (index > 0) InfoDivider(start = 16.dp)
                    Column(Modifier.padding(horizontal = 16.dp, vertical = 15.dp)) {
                        Text(item.name, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(7.dp))
                        CollectLine("用途", item.purpose)
                        CollectLine("场景", item.scene)
                        CollectLine("去向", item.where)
                        CollectLine("控制", item.control)
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun CollectLine(label: String, value: String) {
    Row(Modifier.padding(vertical = 2.dp)) {
        Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.primary, modifier = Modifier.width(36.dp))
        Text(value, fontSize = 12.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 17.sp, modifier = Modifier.weight(1f))
    }
}
