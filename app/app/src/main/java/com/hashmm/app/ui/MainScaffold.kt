package com.hashmm.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.activity.ActivityScreen
import com.hashmm.app.ui.chat.ChatHomeScreen
import com.hashmm.app.ui.profile.ProfileScreen
import com.hashmm.app.ui.workbench.WorkbenchHubScreen

private data class TabItem(val label: String, val icon: ImageVector, val iconSel: ImageVector)

/** Marvis 式底部 Tab 主框架：对话(对话优先) / 动态 / 工作台 / 我的。 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MainScaffold(
    onOpenConversation: (String) -> Unit,
    onOpenRemote: () -> Unit,
    onLogin: () -> Unit,
    onOpenTerms: () -> Unit = {},
    onOpenPrivacy: () -> Unit = {},
    onOpenMemory: () -> Unit = {},
    onOpenKnowledge: () -> Unit = {},
    onNewChat: (String) -> Unit = {},
    onOpenWebClient: () -> Unit = {},
    onOpenModels: () -> Unit = {},
    onOpenKg: () -> Unit = {},
    onOpenAdmin: () -> Unit = {},
    onOpenRelay: () -> Unit = {},
    onOpenUsage: () -> Unit = {},
    onOpenValidity: () -> Unit = {},
    viewModel: MainViewModel = hiltViewModel(),
) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val loggedIn by viewModel.isLoggedIn.collectAsStateWithLifecycle(initialValue = false)
    val tabs = listOf(
        TabItem("对话", Icons.Outlined.ChatBubbleOutline, Icons.Filled.ChatBubble),
        TabItem("动态", Icons.Outlined.Bolt, Icons.Filled.Bolt),
        TabItem("工作台", Icons.Outlined.Dashboard, Icons.Filled.Dashboard),
        TabItem("我的", Icons.Outlined.Person, Icons.Filled.Person),
    )
    val imeVisible = WindowInsets.isImeVisible

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            if (!imeVisible) {
            NavigationBar(
                containerColor = MaterialTheme.colorScheme.surface,
                tonalElevation = 0.dp,
            ) {
                tabs.forEachIndexed { i, t ->
                    val sel = tab == i
                    NavigationBarItem(
                        selected = sel,
                        onClick = { tab = i },
                        icon = { Icon(if (sel) t.iconSel else t.icon, contentDescription = t.label) },
                        label = { Text(t.label, fontSize = 11.sp, fontWeight = if (sel) FontWeight.SemiBold else FontWeight.Normal) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = MaterialTheme.colorScheme.onSurface,
                            selectedTextColor = MaterialTheme.colorScheme.onSurface,
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            indicatorColor = Color.Transparent,
                        ),
                    )
                }
            }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (tab) {
                0 -> ChatHomeScreen(
                    email = viewModel.email(),
                    onOpenConversation = { id -> if (loggedIn) onOpenConversation(id) else onLogin() },
                    onOpenRemote = { if (loggedIn) onOpenRemote() else onLogin() },
                    onSendTask = { text -> if (loggedIn) onNewChat(text) else onLogin() }, // 发送 → 新建原生会话并自动发送
                )
                1 -> if (loggedIn) ActivityScreen(onBack = {}, onOpenConversation = onOpenConversation, showBack = false)
                     else LoginGate("登录后查看动态与历史对话", "未登录时不展示任何账号数据", onLogin)
                2 -> if (loggedIn) WorkbenchHubScreen(
                    onOpenKnowledge = onOpenKnowledge,
                    onOpenMemory = onOpenMemory,
                    onOpenRemote = onOpenRemote,
                    onOpenWebClient = onOpenWebClient,
                    onOpenModels = onOpenModels,
                    onOpenKg = onOpenKg,
                    onOpenAdmin = onOpenAdmin,
                    onOpenRelay = onOpenRelay,
                    onOpenUsage = onOpenUsage,
                    onOpenValidity = onOpenValidity,
                ) else LoginGate("登录后使用工作台", "知识库、知识图谱、记忆中心等均需登录后查看你自己的数据", onLogin)
                else -> ProfileScreen(onOpenRemote = onOpenRemote, onLogin = onLogin, onOpenTerms = onOpenTerms, onOpenPrivacy = onOpenPrivacy, onOpenMemory = onOpenMemory, onOpenKnowledge = onOpenKnowledge)
            }
        }
    }
}

/** 未登录占位：私人页面（工作台/动态）在未登录时显示，引导去登录。 */
@Composable
private fun LoginGate(title: String, subtitle: String, onLogin: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(horizontal = 36.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            Icons.Outlined.Lock, contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(44.dp),
        )
        Spacer(Modifier.height(16.dp))
        Text(title, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.height(8.dp))
        Text(subtitle, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
        Spacer(Modifier.height(22.dp))
        Button(onClick = onLogin) { Text("去登录", fontWeight = FontWeight.SemiBold) }
    }
}
