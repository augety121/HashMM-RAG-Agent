package com.hashmm.app.ui.profile

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.MenuBook
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.components.HashMascot
import com.hashmm.app.ui.settings.SettingsViewModel
import com.hashmm.app.ui.theme.Accent
import com.hashmm.app.ui.theme.AvatarGradient

/** 「我的」—— Marvis 风：头像+昵称 / 吉祥物人设卡 / 极简条目卡 / 退出登录。 */
@Composable
fun ProfileScreen(
    onOpenRemote: () -> Unit,
    onLogin: () -> Unit,
    onOpenTerms: () -> Unit = {},
    onOpenPrivacy: () -> Unit = {},
    onOpenMemory: () -> Unit = {},
    onOpenKnowledge: () -> Unit = {},
    viewModel: SettingsViewModel = hiltViewModel(),
) {
    val loggedIn by viewModel.isLoggedIn.collectAsStateWithLifecycle()
    val clientUrl by viewModel.clientUrl.collectAsStateWithLifecycle()
    val isAutoConfigured by viewModel.isAutoConfigured.collectAsStateWithLifecycle()
    val email = viewModel.email()
    val context = LocalContext.current
    val avatar by viewModel.avatar.collectAsStateWithLifecycle()
    val avatarPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            val bytes = runCatching { context.contentResolver.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
            if (bytes != null && bytes.isNotEmpty()) viewModel.uploadAvatar(bytes) {}
        }
    }
    var urlInput by remember(clientUrl) { mutableStateOf(clientUrl) }
    var saved by remember { mutableStateOf(false) }
    var showUrlEditor by remember { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp),
    ) {
        Spacer(Modifier.height(30.dp))

        // ── 用户卡（头像 + 在线徽标 + 账号）──
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box(contentAlignment = Alignment.Center) {
                Box(
                    Modifier.size(64.dp).clip(RoundedCornerShape(22.dp)).background(AvatarGradient)
                        .clickable(enabled = loggedIn) { avatarPicker.launch("image/*") },
                    contentAlignment = Alignment.Center,
                ) {
                    val a = avatar
                    if (a != null) {
                        Image(
                            bitmap = a,
                            contentDescription = "头像",
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.size(64.dp).clip(RoundedCornerShape(22.dp)),
                        )
                    } else {
                        Text(
                            (email?.substringBefore("@")?.firstOrNull()?.uppercaseChar() ?: '#').toString(),
                            color = Color.White, fontWeight = FontWeight.ExtraBold, fontSize = 28.sp,
                        )
                    }
                }
                if (loggedIn) {
                    // 右上角相机角标：提示可点头像更换
                    Box(
                        Modifier.align(Alignment.TopEnd).offset(x = 4.dp, y = (-4).dp)
                            .size(22.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(Icons.Outlined.PhotoCamera, contentDescription = null,
                            tint = Color.White, modifier = Modifier.size(13.dp))
                    }
                    // 右下角在线徽标
                    Box(
                        Modifier.align(Alignment.BottomEnd).offset(x = 3.dp, y = 3.dp)
                            .size(18.dp).clip(CircleShape).background(MaterialTheme.colorScheme.background),
                        contentAlignment = Alignment.Center,
                    ) { Box(Modifier.size(12.dp).clip(CircleShape).background(Color(0xFF22C55E))) }
                }
            }
            Spacer(Modifier.width(16.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    email?.substringBefore("@") ?: "未登录",
                    fontSize = 21.sp, fontWeight = FontWeight.ExtraBold,
                    color = MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(3.dp))
                Text(
                    if (loggedIn) (email ?: "") else "点击下方登录，同步桌面数据",
                    fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                if (loggedIn) {
                    Spacer(Modifier.height(8.dp))
                    Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                        Row(
                            Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(Modifier.size(6.dp).clip(CircleShape).background(Color(0xFF22C55E)))
                            Spacer(Modifier.width(5.dp))
                            Text("已登录 · 与桌面客户端通用", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(26.dp))

        // ── 吉祥物人设卡 ──
        Card(
            shape = RoundedCornerShape(22.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            modifier = Modifier.fillMaxWidth()
                .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(22.dp)),
        ) {
            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                HashMascot(Modifier.size(92.dp))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("小哈", fontSize = 19.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "HashMM 的小助手，为你 24 小时在线，桌面在跑就能随时差遣我。",
                        fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        // ── 连接与设备 ──
        SectionLabel("连接与设备")
        Card(
            shape = RoundedCornerShape(18.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            modifier = Modifier.fillMaxWidth()
                .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
        ) {
            Column {
                ProfileRow(Icons.Outlined.Computer, "远程控制", null, onOpenRemote)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(
                    Icons.Outlined.Link, "客户端连接",
                    when {
                        isAutoConfigured -> "已自动配置"
                        clientUrl.isBlank() -> "未配置"
                        else -> "已连接"
                    },
                ) { showUrlEditor = !showUrlEditor }
            }
        }

        if (showUrlEditor) {
            Spacer(Modifier.height(10.dp))
            Card(
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth()
                    .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
            ) {
                Column(Modifier.padding(18.dp)) {
                    Text(
                        if (isAutoConfigured) "已从云端自动获取后端地址，无需手填。如需自定义可在下方修改。"
                        else "桌面客户端的公网地址（如 Cloudflare Tunnel 域名）。后端配好后会自动同步到这里，一般无需手填。",
                        fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(
                        value = urlInput,
                        onValueChange = { urlInput = it; saved = false },
                        placeholder = { Text("https://your-tunnel.example.com") },
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = { viewModel.saveUrl(urlInput.trim()); saved = true },
                        enabled = urlInput.isNotBlank(),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().height(48.dp),
                    ) { Text(if (saved) "已保存" else "保存") }
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        // ── 工作区 ──
        SectionLabel("工作区")
        Card(
            shape = RoundedCornerShape(18.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            modifier = Modifier.fillMaxWidth()
                .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
        ) {
            Column {
                ProfileRow(Icons.Outlined.Psychology, "记忆中心", "云端同步", onOpenMemory)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(Icons.Outlined.MenuBook, "知识库", "语料 · 文档", onOpenKnowledge)
            }
        }

        Spacer(Modifier.height(16.dp))

        // ── 关于 ──
        SectionLabel("关于")
        Card(
            shape = RoundedCornerShape(18.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            modifier = Modifier.fillMaxWidth()
                .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
        ) {
            Column {
                ProfileRow(Icons.Outlined.Description, "用户协议", null, onOpenTerms)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(Icons.Outlined.Shield, "隐私政策", null, onOpenPrivacy)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(Icons.Outlined.Info, "关于 HashMM", "v1.10.24", {})
            }
        }

        Spacer(Modifier.height(22.dp))

        // ── 登录 / 退出登录 ──
        if (loggedIn) {
            Card(
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth()
                    .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(18.dp)),
            ) {
                TextButton(
                    onClick = { viewModel.signOut() },
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                ) { Text("退出登录", color = Accent, fontWeight = FontWeight.SemiBold) }
            }
        } else {
            Button(
                onClick = onLogin,
                shape = RoundedCornerShape(14.dp),
                colors = androidx.compose.material3.ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                ),
                modifier = Modifier.fillMaxWidth().height(52.dp),
            ) { Text("登录", fontSize = 16.sp, fontWeight = FontWeight.SemiBold) }
        }

        Spacer(Modifier.height(28.dp))
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text,
        fontSize = 12.sp,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        letterSpacing = 0.5.sp,
        modifier = Modifier.padding(start = 4.dp, top = 2.dp, bottom = 8.dp),
    )
}

@Composable
private fun ProfileRow(icon: ImageVector, title: String, trailing: String?, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 18.dp, vertical = 17.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(23.dp))
        Spacer(Modifier.width(14.dp))
        Text(title, fontSize = 16.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
        if (trailing != null) {
            Text(trailing, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(6.dp))
        }
        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
