package com.hashmm.app.ui.profile

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
import androidx.compose.material.icons.automirrored.outlined.FactCheck
import androidx.compose.material.icons.automirrored.outlined.HelpOutline
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.components.HashMascot
import com.hashmm.app.ui.components.HmmCard
import com.hashmm.app.ui.settings.SettingsViewModel
import com.hashmm.app.ui.theme.Accent
import com.hashmm.app.ui.theme.AvatarGradient
import com.hashmm.app.ui.theme.AppFont

/** 「我的」—— Marvis 风：头像+昵称 / 吉祥物人设卡 / 极简条目卡 / 退出登录。 */
@Composable
fun ProfileScreen(
    onOpenRemote: () -> Unit,
    onLogin: () -> Unit,
    onOpenMemory: () -> Unit = {},
    onOpenKnowledge: () -> Unit = {},
    onOpenClientConn: () -> Unit = {},
    onOpenModels: () -> Unit = {},
    onOpenPersonalInfo: () -> Unit = {},
    onOpenAbout: () -> Unit = {},
    onOpenHelp: () -> Unit = {},
    onOpenDataList: () -> Unit = {},
    viewModel: SettingsViewModel = hiltViewModel(),
) {
    val loggedIn by viewModel.isLoggedIn.collectAsStateWithLifecycle()
    val clientUrl by viewModel.clientUrl.collectAsStateWithLifecycle()
    val isAutoConfigured by viewModel.isAutoConfigured.collectAsStateWithLifecycle()
    val email = viewModel.email()
    val avatar by viewModel.avatar.collectAsStateWithLifecycle()
    val displayName by viewModel.displayName.collectAsStateWithLifecycle()   // V236: 昵称优先
    val shownName = displayName.ifBlank { email?.substringBefore("@") ?: "未登录" }
    val initial = (displayName.firstOrNull() ?: email?.substringBefore("@")?.firstOrNull() ?: '#')

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
                        .clickable(enabled = loggedIn) { onOpenPersonalInfo() },
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
                            initial.toString().uppercase(),
                            color = Color.White, fontWeight = FontWeight.Bold, fontSize = AppFont.screenTitle,
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
                        Icon(Icons.Outlined.PhotoCamera, contentDescription = "编辑个人信息",
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
                    shownName,
                    fontSize = AppFont.screenTitle, fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurface, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(3.dp))
                Text(
                    if (loggedIn) (email ?: "") else "点击下方登录，同步桌面数据",
                    fontSize = AppFont.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                if (loggedIn) {
                    Spacer(Modifier.height(8.dp))
                    Surface(shape = RoundedCornerShape(20.dp), color = Color(0xFF22C55E).copy(alpha = 0.10f)) {
                        Row(
                            Modifier.padding(horizontal = 11.dp, vertical = 5.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(Modifier.size(6.dp).clip(CircleShape).background(Color(0xFF22C55E)))
                            Spacer(Modifier.width(6.dp))
                            Text("已登录 · 与桌面客户端通用", fontSize = AppFont.label, fontWeight = FontWeight.Medium, color = Color(0xFF16A34A))
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(26.dp))

        // ── 吉祥物人设卡 ──
        HmmCard(padding = false) {
            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                HashMascot(Modifier.size(68.dp))
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text("小哈", fontSize = AppFont.sectionTitle, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "HashMM 的小助手，为你 24 小时在线，桌面在跑就能随时差遣我。",
                        fontSize = AppFont.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        // ── 连接与设备 ──
        SectionLabel("连接与设备")
        HmmCard(padding = false) {
            Column {
                ProfileRow(Icons.Outlined.Computer, "远程控制", "投屏 · 操控电脑", onOpenRemote)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(
                    Icons.Outlined.Link, "客户端连接",
                    when {
                        isAutoConfigured -> "已自动配置"
                        clientUrl.isBlank() -> "未配置"
                        else -> "已配置"
                    },
                    onOpenClientConn,
                )
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(Icons.Outlined.Tune, "我的 API 与模型", "仅当前账号可见和可用", onOpenModels)
            }
        }

        Spacer(Modifier.height(16.dp))

        // ── 账号 ──
        SectionLabel("账号")
        HmmCard(padding = false) {
            ProfileRow(Icons.Outlined.Person, "个人信息", "头像 · 昵称 · 账号", onOpenPersonalInfo)
        }

        Spacer(Modifier.height(16.dp))

        // ── 关于 ──
        SectionLabel("关于")
        HmmCard(padding = false) {
            Column {
                ProfileRow(Icons.AutoMirrored.Outlined.HelpOutline, "帮助与反馈", null, onOpenHelp)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(Icons.AutoMirrored.Outlined.FactCheck, "个人信息采集清单", null, onOpenDataList)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.22f), modifier = Modifier.padding(horizontal = 18.dp))
                ProfileRow(Icons.Outlined.Info, "关于 HashMM", "v" + com.hashmm.app.BuildConfig.VERSION_NAME, onOpenAbout)
            }
        }

        Spacer(Modifier.height(22.dp))

        // ── 登录 / 退出登录 ──
        if (loggedIn) {
            HmmCard(padding = false) {
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
            ) { Text("登录", fontSize = AppFont.body, fontWeight = FontWeight.SemiBold) }
        }

        Spacer(Modifier.height(28.dp))
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text,
        fontSize = AppFont.caption,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        letterSpacing = 0.5.sp,
        modifier = Modifier.padding(start = 4.dp, top = 2.dp, bottom = 8.dp),
    )
}

@Composable
private fun ProfileRow(icon: ImageVector, title: String, trailing: String?, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // V251 按用户要求去掉灰底方块——图标直接墨黑裸放，更干净（Marvis 我的页列表行风格）
        Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.onSurface, modifier = Modifier.size(22.dp))
        Spacer(Modifier.width(15.dp))
        Text(title, fontSize = AppFont.cardTitle, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
        if (trailing != null) {
            Text(trailing, fontSize = AppFont.subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(6.dp))
        }
        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f))
    }
}
