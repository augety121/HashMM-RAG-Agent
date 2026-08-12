package com.hashmm.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.hashmm.app.ui.auth.LoginScreen
import com.hashmm.app.ui.chat.ChatDetailScreen
import com.hashmm.app.ui.legal.PrivacyScreen
import com.hashmm.app.ui.legal.TermsScreen
import com.hashmm.app.ui.memory.MemoryScreen
import com.hashmm.app.ui.knowledge.KnowledgeScreen
import com.hashmm.app.ui.models.ModelConfigScreen
import com.hashmm.app.ui.kg.KGScreen
import com.hashmm.app.ui.admin.AdminScreen
import com.hashmm.app.ui.relay.RelayScreen
import com.hashmm.app.ui.usage.UsageScreen
import com.hashmm.app.ui.validity.ValidityScreen
import com.hashmm.app.ui.remote.RemoteControlScreen
import com.hashmm.app.ui.tasks.ComputerTaskLaunchScreen

@Composable
fun HashMMApp() {
    val nav = rememberNavController()
    val photoVm: PhotoRequestViewModel = hiltViewModel()

    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Box(Modifier.fillMaxSize().testTag("hashmm-root")) {
            NavHost(navController = nav, startDestination = Routes.MAIN) {

            composable(Routes.MAIN) {
                MainScaffold(
                    onOpenConversation = { convId -> nav.navigate(Routes.chatDetail(convId)) },
                    onNewChat = { text -> nav.navigate(Routes.chatDetail(java.util.UUID.randomUUID().toString(), text)) },
                    onNewChatDispatch = { text, kind -> nav.navigate(Routes.chatDetail(java.util.UUID.randomUUID().toString(), text, kind)) },
                    onOpenTaskLauncher = { kind, preset -> nav.navigate(Routes.taskLaunch(kind, preset)) },
                    onOpenRemote = { nav.navigate(Routes.REMOTE) },
                    onLogin = { nav.navigate(Routes.login(Routes.MAIN)) },
                    onOpenMemory = { nav.navigate(Routes.MEMORY) },
                    onOpenKnowledge = { nav.navigate(Routes.KNOWLEDGE) },
                    onOpenScheduled = { nav.navigate(Routes.SCHEDULED) },
                    onOpenRuns = { nav.navigate(Routes.RUNS) },
                    onOpenWork = { nav.navigate(Routes.workCanvas(it)) },
                    onOpenAudit = { nav.navigate(Routes.AUDIT) },
                    onOpenQuality = { nav.navigate(Routes.QUALITY) },
                    onOpenSelfTest = { nav.navigate(Routes.SELFTEST) },
                    onOpenContext = { nav.navigate(Routes.CONTEXT) },
                    onOpenAdvanced = { nav.navigate(Routes.ADVANCED) },
                    onOpenModels = { nav.navigate(Routes.MODELS) },
                    onOpenKg = { nav.navigate(Routes.KG) },
                    onOpenAdmin = { nav.navigate(Routes.ADMIN) },
                    onOpenRelay = { nav.navigate(Routes.RELAY) },
                    onOpenUsage = { nav.navigate(Routes.USAGE) },
                    onOpenAgentsStudio = { nav.navigate(Routes.AGENTS_STUDIO) },
                    onOpenDocStudio = { nav.navigate(Routes.DOC_STUDIO) },
                    onOpenControlHub = { nav.navigate(Routes.CONTROL_HUB) },
                    onOpenValidity = { nav.navigate(Routes.VALIDITY) },
                    onOpenClientConn = { nav.navigate(Routes.CLIENT_CONN) },
                    onOpenPersonalInfo = { nav.navigate(Routes.PERSONAL_INFO) },
                    onOpenAbout = { nav.navigate(Routes.ABOUT) },
                    onOpenHelp = { nav.navigate(Routes.HELP) },
                    onOpenDataList = { nav.navigate(Routes.DATA_LIST) },
                )
            }

            composable(
                route = Routes.LOGIN_ROUTE,
                arguments = listOf(navArgument("next") {
                    type = NavType.StringType
                    defaultValue = Routes.MAIN
                }),
            ) { entry ->
                val next = entry.arguments?.getString("next") ?: Routes.MAIN
                LoginScreen(
                    onLoggedIn = {
                        nav.navigate(next) {
                            popUpTo(Routes.LOGIN_ROUTE) { inclusive = true }
                            launchSingleTop = true
                        }
                    },
                    onBack = { nav.popBackStack() },
                    onOpenTerms = { nav.navigate(Routes.TERMS) },
                    onOpenPrivacy = { nav.navigate(Routes.PRIVACY) },
                )
            }

            composable(
                route = Routes.CHAT_DETAIL_ROUTE,
                arguments = listOf(
                    navArgument("convId") { type = NavType.StringType },
                    navArgument("initial") { type = NavType.StringType; defaultValue = "" },
                    navArgument("dispatch") { type = NavType.StringType; defaultValue = "" },
                ),
            ) {
                ChatDetailScreen(
                    onBack = { nav.popBackStack() },
                    onOpenAgentsStudio = { convId, goal -> nav.navigate(Routes.agentsStudio(convId, goal)) },
                )
            }

            composable(
                route = Routes.TASK_LAUNCH_ROUTE,
                arguments = listOf(
                    navArgument("kind") { type = NavType.StringType },
                    navArgument("preset") { type = NavType.StringType; defaultValue = "" },
                ),
            ) { entry ->
                ComputerTaskLaunchScreen(
                    kind = entry.arguments?.getString("kind").orEmpty(),
                    preset = entry.arguments?.getString("preset").orEmpty(),
                    onBack = { nav.popBackStack() },
                    onLaunch = { prompt, dispatchKind ->
                        nav.navigate(Routes.chatDetail(java.util.UUID.randomUUID().toString(), prompt, dispatchKind))
                    },
                )
            }

            composable(Routes.REMOTE) {
                RemoteControlScreen(onBack = { nav.popBackStack() })
            }

            composable(Routes.CLIENT_CONN) { com.hashmm.app.ui.profile.ClientConnectionScreen(onBack = { nav.popBackStack() }) }

            composable(Routes.PERSONAL_INFO) { com.hashmm.app.ui.profile.PersonalInfoScreen(onBack = { nav.popBackStack() }) }

            composable(Routes.ABOUT) {
                com.hashmm.app.ui.profile.AboutScreen(
                    onBack = { nav.popBackStack() },
                    onOpenTerms = { nav.navigate(Routes.TERMS) },
                    onOpenPrivacy = { nav.navigate(Routes.PRIVACY) },
                )
            }

            composable(Routes.HELP) { com.hashmm.app.ui.profile.HelpFeedbackScreen(onBack = { nav.popBackStack() }) }

            composable(Routes.DATA_LIST) { com.hashmm.app.ui.profile.DataCollectionScreen(onBack = { nav.popBackStack() }) }

            composable(Routes.TERMS) { TermsScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.PRIVACY) { PrivacyScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.MEMORY) { MemoryScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.SCHEDULED) { com.hashmm.app.ui.workbench.ScheduledScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.RUNS) {
                com.hashmm.app.ui.workbench.RunsScreen(
                    onBack = { nav.popBackStack() },
                    onOpenConversation = { nav.navigate(Routes.chatDetail(it)) },
                )
            }
            composable(
                route = Routes.WORK_CANVAS_ROUTE,
                arguments = listOf(navArgument("runId") { type = NavType.StringType }),
            ) { entry ->
                com.hashmm.app.ui.workbench.WorkCanvasScreen(
                    runId = entry.arguments?.getString("runId").orEmpty(),
                    onBack = { nav.popBackStack() },
                    onOpenConversation = { nav.navigate(Routes.chatDetail(it)) },
                )
            }
            composable(Routes.AUDIT) { com.hashmm.app.ui.workbench.AuditScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.QUALITY) {
                com.hashmm.app.ui.workbench.QualityScreen(
                    onBack = { nav.popBackStack() },
                    onOpenRuns = { nav.navigate(Routes.RUNS) },
                )
            }
            composable(Routes.SELFTEST) { com.hashmm.app.ui.workbench.SelfTestScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.CONTEXT) {
                com.hashmm.app.ui.workbench.ContextScreen(
                    onBack = { nav.popBackStack() },
                    onOpenConversation = { nav.navigate(Routes.chatDetail(it)) },
                )
            }
            composable(Routes.ADVANCED) {
                com.hashmm.app.ui.workbench.AdvancedScreen(
                    onBack = { nav.popBackStack() },
                    onOpenTaskLauncher = { kind, preset -> nav.navigate(Routes.taskLaunch(kind, preset)) },
                    onOpenAgentsStudio = { nav.navigate(Routes.AGENTS_STUDIO) },
                )
            }
            composable(Routes.KNOWLEDGE) { KnowledgeScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.MODELS) { ModelConfigScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.KG) { KGScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.ADMIN) { AdminScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.RELAY) { RelayScreen(onBack = { nav.popBackStack() }) }
            composable(
                route = Routes.AGENTS_STUDIO_ROUTE,
                arguments = listOf(
                    navArgument("convId") { type = NavType.StringType; defaultValue = "" },
                    navArgument("goal") { type = NavType.StringType; defaultValue = "" },
                ),
            ) { entry ->
                com.hashmm.app.ui.studio.AgentsStudioScreen(
                    existingConversationId = entry.arguments?.getString("convId").orEmpty(),
                    initialGoal = entry.arguments?.getString("goal").orEmpty(),
                    onBack = { nav.popBackStack() },
                    onOpenConversation = { nav.navigate(Routes.chatDetail(it)) },
                )
            }
            composable(Routes.DOC_STUDIO) {
                com.hashmm.app.ui.studio.DocStudioScreen(
                    onBack = { nav.popBackStack() },
                    onOpenConversation = { nav.navigate(Routes.chatDetail(it)) },
                )
            }
            composable(Routes.CONTROL_HUB) { com.hashmm.app.ui.studio.ControlHubScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.USAGE) { UsageScreen(onBack = { nav.popBackStack() }) }
            composable(Routes.VALIDITY) { ValidityScreen(onBack = { nav.popBackStack() }) }
            }
            // 全局：电脑端向手机要照片的隐私确认弹窗
            PhotoRequestConsent(photoVm)
        }
    }
}

/** 电脑端请求手机照片时弹出的确认框：用户同意（并授予相册权限）才读取并发送最近一张照片。 */
@Composable
private fun PhotoRequestConsent(vm: PhotoRequestViewModel) {
    val ctx = LocalContext.current
    val pending by vm.pending.collectAsState()
    val sent by vm.sent.collectAsState()

    val permLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) vm.approve(ctx.contentResolver)
        else { Toast.makeText(ctx, "未授予相册权限，无法发送照片", Toast.LENGTH_SHORT).show(); vm.deny() }
    }
    fun requestApprove() {
        val perm = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_IMAGES
                   else Manifest.permission.READ_EXTERNAL_STORAGE
        if (ctx.checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED)
            vm.approve(ctx.contentResolver)
        else permLauncher.launch(perm)
    }

    LaunchedEffect(sent) { if (sent > 0) Toast.makeText(ctx, "照片已发送到电脑端对话", Toast.LENGTH_SHORT).show() }

    pending?.let { _ ->
        AlertDialog(
            onDismissRequest = { vm.deny() },
            title = { Text("电脑端请求照片") },
            text = { Text("你的电脑端正在请求发送手机相册里最近的一张照片。\n\n同意后仅会读取最近一张照片并发送到当前对话，不发送任何其它内容。") },
            confirmButton = { TextButton(onClick = { requestApprove() }) { Text("同意发送") } },
            dismissButton = { TextButton(onClick = { vm.deny() }) { Text("拒绝") } },
        )
    }
}
