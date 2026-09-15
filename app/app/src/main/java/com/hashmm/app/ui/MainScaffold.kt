package com.hashmm.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.Button
import androidx.compose.material3.Surface
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.remember
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.activity.ActivityScreen
import com.hashmm.app.ui.chat.ChatHomeScreen
import com.hashmm.app.ui.profile.ProfileScreen
import com.hashmm.app.ui.workbench.WorkbenchHubScreen

private data class TabItem(val label: String, val icon: ImageVector, val iconSel: ImageVector)

/** 面向用户的四个目的地：表达目标 / 处理今天 / 组织工作 / 管理个人设置。 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MainScaffold(
    onOpenConversation: (String) -> Unit,
    onOpenRemote: () -> Unit,
    onLogin: () -> Unit,
    onOpenMemory: () -> Unit = {},
    onOpenKnowledge: () -> Unit = {},
    onNewChat: (String) -> Unit = {},
    onNewChatDispatch: (String, String) -> Unit = { _, _ -> },
    onOpenTaskLauncher: (String, String) -> Unit = { _, _ -> },
    onOpenAgentsStudio: () -> Unit = {},      // V259 原生三工坊
    onOpenDocStudio: () -> Unit = {},
    onOpenControlHub: () -> Unit = {},
    onOpenScheduled: () -> Unit = {},
    onOpenRuns: () -> Unit = {},
    onOpenWork: (String) -> Unit = {},
    onOpenAudit: () -> Unit = {},
    onOpenQuality: () -> Unit = {},
    onOpenSelfTest: () -> Unit = {},
    onOpenContext: () -> Unit = {},
    onOpenAdvanced: () -> Unit = {},
    onOpenModels: () -> Unit = {},
    onOpenKg: () -> Unit = {},
    onOpenAdmin: () -> Unit = {},
    onOpenRelay: () -> Unit = {},
    onOpenUsage: () -> Unit = {},
    onOpenValidity: () -> Unit = {},
    onOpenClientConn: () -> Unit = {},
    onOpenPersonalInfo: () -> Unit = {},
    onOpenAbout: () -> Unit = {},
    onOpenHelp: () -> Unit = {},
    onOpenDataList: () -> Unit = {},
    viewModel: MainViewModel = hiltViewModel(),
) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val loggedIn by viewModel.isLoggedIn.collectAsStateWithLifecycle(initialValue = false)
    val tabs = listOf(
        TabItem("对话", Icons.Outlined.ChatBubbleOutline, Icons.Filled.ChatBubble),
        TabItem("今天", Icons.Outlined.Bolt, Icons.Filled.Bolt),
        TabItem("工作", Icons.Outlined.Dashboard, Icons.Filled.Dashboard),
        TabItem("我的", Icons.Outlined.Person, Icons.Filled.Person),
    )
    val imeVisible = WindowInsets.isImeVisible

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            if (!imeVisible) {
                // V213: 收敛底部导航体量（用户反馈"四个 tab 有点大"）——
                // M3 NavigationBar 固定 80dp 且内部尺寸不可调，改为自绘紧凑条：
                // 总高 58dp + 系统手势/按键 inset，图标 22dp、文字 10.5sp，选中主色。
                CompactNavBar(tabs = tabs, selected = tab, onSelect = { tab = it })
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
                    onComputerTask = { task, kind -> if (loggedIn) onNewChatDispatch(task, kind) else onLogin() }, // 电脑任务 → 下发到电脑客户端
                )
                1 -> if (loggedIn) ActivityScreen(
                    onBack = {},
                    onOpenConversation = onOpenConversation,
                    onNewChat = { text -> if (loggedIn) onNewChat(text) else onLogin() },
                    onOpenRemote = { if (loggedIn) onOpenRemote() else onLogin() },
                    onOpenTaskLauncher = { kind, preset -> if (loggedIn) onOpenTaskLauncher(kind, preset) else onLogin() },
                    showBack = false,
                    onOpenRuns = onOpenRuns,
                    onOpenWork = onOpenWork,
                    onOpenScheduled = onOpenScheduled,
                )
                     else LoginGate("登录后查看今天", "授权、补充信息和成果验收只对当前账号可见", onLogin)
                2 -> if (loggedIn) WorkbenchHubScreen(
                    isAdmin = viewModel.isAdmin(),
                    onOpenKnowledge = onOpenKnowledge,
                    onOpenMemory = onOpenMemory,
                    onNewChat = { text -> if (loggedIn) onNewChat(text) else onLogin() },
                    onOpenAgentsStudio = onOpenAgentsStudio,
                    onOpenDocStudio = onOpenDocStudio,
                    onOpenControlHub = onOpenControlHub,
                    onOpenScheduled = onOpenScheduled,
                    onOpenRuns = onOpenRuns,
                    onOpenWork = onOpenWork,
                    onOpenAudit = onOpenAudit,
                    onOpenQuality = onOpenQuality,
                    onOpenSelfTest = onOpenSelfTest,
                    onOpenContext = onOpenContext,
                    onOpenAdvanced = onOpenAdvanced,
                    onOpenModels = onOpenModels,
                    onOpenKg = onOpenKg,
                    onOpenAdmin = onOpenAdmin,
                    onOpenRelay = onOpenRelay,
                    onOpenUsage = onOpenUsage,
                    onOpenValidity = onOpenValidity,
                ) else LoginGate("登录后查看工作", "手机和桌面端会显示同一项工作的进度与结果", onLogin)
                else -> ProfileScreen(
                    onOpenRemote = onOpenRemote, onLogin = onLogin,
                    onOpenMemory = onOpenMemory, onOpenKnowledge = onOpenKnowledge,
                    onOpenClientConn = onOpenClientConn, onOpenModels = onOpenModels,
                    onOpenPersonalInfo = onOpenPersonalInfo,
                    onOpenAbout = onOpenAbout, onOpenHelp = onOpenHelp, onOpenDataList = onOpenDataList,
                )
            }
        }
    }
}

/** V213: 紧凑底部导航（58dp）——手绘替代 M3 NavigationBar（其 80dp 行高不可调）。
 *  insets 处理：windowInsetsPadding(navigationBars) 在 height 之前，条体恒 58dp、
 *  手势/三键区自然外扩，不裁内容。选中：填充图标 + 主色；未选：线框图标 + 变体色。 */
@Composable
private fun CompactNavBar(tabs: List<TabItem>, selected: Int, onSelect: (Int) -> Unit) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surface, tonalElevation = 0.dp) {
        Row(
            Modifier
                .fillMaxWidth()
                .windowInsetsPadding(WindowInsets.navigationBars)
                .height(58.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            tabs.forEachIndexed { i, t ->
                val sel = selected == i
                // V243 微交互：选中色 150ms 平滑过渡（原先瞬间跳变），大厂标签栏的"顺滑感"
                val tint by androidx.compose.animation.animateColorAsState(
                    targetValue = if (sel) cs.primary else cs.onSurfaceVariant,
                    animationSpec = androidx.compose.animation.core.tween(150),
                    label = "tabTint",
                )
                Column(
                    Modifier
                        .weight(1f)
                        .fillMaxHeight()
                        .clickable(
                            interactionSource = remember { androidx.compose.foundation.interaction.MutableInteractionSource() },
                            indication = null,   // V251 去掉铺满整格的灰色方形涟漪（大厂标签栏纯色切换、无涟漪）
                        ) { onSelect(i) },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    Icon(
                        if (sel) t.iconSel else t.icon,
                        contentDescription = t.label,
                        tint = tint,
                        modifier = Modifier.size(22.dp),
                    )
                    Spacer(Modifier.height(2.dp))
                    Text(
                        t.label,
                        fontSize = 10.5.sp,
                        fontWeight = if (sel) FontWeight.SemiBold else FontWeight.Normal,
                        color = tint,
                    )
                }
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
