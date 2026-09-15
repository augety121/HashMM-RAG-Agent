package com.hashmm.app.ui.workbench

import android.annotation.SuppressLint
import android.webkit.WebView
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.OpenInBrowser
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.WifiOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.components.HashMascot
import java.net.URLEncoder

/**
 * HashMM 工作台：在 App 内嵌入 HashMM 桌面客户端（frontend-next），用同一个 Supabase 会话登录。
 * 无内层 Scaffold（顶部 inset 由外层 MainScaffold 统一处理）。
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WorkbenchScreen(
    onBack: () -> Unit,
    showBack: Boolean = true,
    initialView: String = "",     // V216: 直达某桌面面板（usage/audit/runs/scheduled/quality/advanced…）
    viewModel: WorkbenchViewModel = hiltViewModel()
) {
    val clientUrl by viewModel.clientUrl.collectAsStateWithLifecycle()
    var webView by remember { mutableStateOf<WebView?>(null) }
    var loading by remember { mutableStateOf(true) }
    var entryUrl by remember(clientUrl, initialView) { mutableStateOf("") }
    var loadError by remember { mutableStateOf<String?>(null) }   // V213: 主帧加载失败 → 全屏重试

    LaunchedEffect(clientUrl, initialView) {
        entryUrl = ""
        if (clientUrl.isBlank()) return@LaunchedEffect
        loading = true
        loadError = null
        val target = buildUrl(clientUrl, view = initialView)
        viewModel.webViewEntryUrl(target)
            .onSuccess { entryUrl = it }
            .onFailure { loadError = it.message ?: "工作台身份联动失败"; loading = false }
    }

    BackHandler(enabled = clientUrl.isNotBlank()) {
        val wv = webView
        if (wv != null && wv.canGoBack()) wv.goBack() else onBack()
    }

    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        // Marvis 风标题头
        Row(
            Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (showBack) {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回")
                }
                Spacer(Modifier.width(4.dp))
            }
            Column(Modifier.weight(1f)) {
                Text("工作台", fontSize = 24.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
                Text("电脑端客户端 · 全部模块", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            // V213: 刷新（WebView 里没有浏览器地址栏，给个显式重载入口）
            if (clientUrl.isNotBlank()) {
                // V221 兜底：WebView 白屏时可换系统浏览器打开同一地址（带登录态）
                val ctx = androidx.compose.ui.platform.LocalContext.current
                IconButton(onClick = {
                    try {
                        // V235 修复：token/refresh 原声明在 WebView factory 内部作用域（105 行），
                        // 顶栏此处够不着——同源自取（viewModel 同层可见，取法与 factory 完全一致）。
                        val token = viewModel.accessToken().orEmpty()
                        val u = buildUrl(clientUrl, view = initialView)
                        ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(u)))
                    } catch (_: Exception) { }
                }) { Icon(Icons.Outlined.OpenInBrowser, contentDescription = "在浏览器打开") }
                IconButton(onClick = { loadError = null; webView?.reload() }) {
                    Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
                }
            }
        }

        if (clientUrl.isBlank()) {
            ClientUrlSetup(onSave = viewModel::saveUrl)
        } else {
            Box(Modifier.fillMaxSize()) {
                if (entryUrl.isNotBlank()) AndroidView(
                    modifier = Modifier.fillMaxSize(),
                    factory = { ctx ->
                        WebView(ctx).apply {
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.databaseEnabled = true
                            // 桌面端 UI 在手机上缩放适配（否则按桌面宽度渲染、可视区可能空白）+ 允许缩放
                            settings.useWideViewPort = true
                            settings.loadWithOverviewMode = true
                            settings.setSupportZoom(true)
                            settings.builtInZoomControls = true
                            settings.displayZoomControls = false
                            // V306 修 APP-P0-03：混合内容从 ALWAYS_ALLOW 收紧为 COMPATIBILITY（不再无条件放行
                            // HTTPS 页里的 HTTP 子资源）；并禁掉 file/content 通用访问，缩小 WebView 攻击面。
                            settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
                            settings.allowFileAccess = false
                            settings.allowContentAccess = false
                            @Suppress("DEPRECATION")
                            settings.allowFileAccessFromFileURLs = false
                            @Suppress("DEPRECATION")
                            settings.allowUniversalAccessFromFileURLs = false
                            // 受信基址：只有加载"配置的后端 origin"下的页面，才注入令牌 / 才允许在 WebView 内导航。
                            val trustedBase = clientUrl
                            // Credentials are exchanged by a one-time code before
                            // the WebView starts; native code never injects them.
                            val token = ""
                            val refresh = ""
                            webViewClient = object : WebViewClient() {
                                override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                                    super.onPageStarted(view, url, favicon)
                                    loading = true
                                    // V306：仅当【当前页面确属受信后端 origin】才注入令牌，杜绝导航到第三方页后
                                    // 把 access/refresh 注入到不受信页面的 localStorage（原来对任何页面都注入）。
                                    if (token.isNotBlank() && WebViewSecurity.isSameOrigin(url, trustedBase)) {
                                        val safe = token.replace("'", "\\'")
                                        val safeR = refresh.replace("'", "\\'")
                                        view?.evaluateJavascript(
                                            "try{localStorage.setItem('hmm_token','$safe');" +
                                                "if('$safeR')localStorage.setItem('hmm_refresh','$safeR');" +
                                                "localStorage.setItem('hmm_login_at',String(Date.now()));}catch(e){}", null
                                        )
                                    }
                                }
                                // V306：导航白名单——受信 origin 内的导航照常在 WebView 加载；离开受信 origin 的
                                // http(s) 一律改用系统浏览器打开（不带令牌、不进 WebView），其它 scheme 拒绝。
                                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                                    val target = request?.url?.toString() ?: return false
                                    if (WebViewSecurity.isSameOrigin(target, trustedBase)) return false  // 同源→WebView 内处理
                                    return try {
                                        if (target.startsWith("http://") || target.startsWith("https://")) {
                                            ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(target)))
                                        }
                                        true   // 非同源一律不在 WebView 内加载（拦截）
                                    } catch (e: Exception) { true }
                                }
                                override fun onPageFinished(view: WebView?, url: String?) {
                                    super.onPageFinished(view, url)
                                    loading = false
                                }
                                override fun onReceivedError(
                                    view: WebView?,
                                    request: WebResourceRequest?,
                                    error: WebResourceError?,
                                ) {
                                    super.onReceivedError(view, request, error)
                                    // 只在主帧失败时给全屏错误态；子资源失败（图标/字体）不打扰
                                    if (request?.isForMainFrame == true) {
                                        loading = false
                                        loadError = error?.description?.toString() ?: "加载失败"
                                    }
                                }
                            }
                            // V218 白屏诊断：网页控制台错误落 Logcat（tag=HashMMWeb），下轮据此定位
                            webChromeClient = object : android.webkit.WebChromeClient() {
                                override fun onConsoleMessage(m: android.webkit.ConsoleMessage?): Boolean {
                                    android.util.Log.i("HashMMWeb", (m?.message() ?: "") + " @" + (m?.sourceId() ?: "") + ":" + (m?.lineNumber() ?: 0))
                                    return true
                                }
                            }
                            webView = this
                            loadUrl(entryUrl)
                        }
                    },
                    update = { }
                )
                // V266 加载态重设计：首屏不再白屏——居中品牌标识 + 柔和进度 + 提示，
                // WebView 就绪后淡出。比之前"顶部一条线+底下大白板"体面得多。
                androidx.compose.animation.AnimatedVisibility(
                    visible = loading && loadError == null,
                    exit = androidx.compose.animation.fadeOut(),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    Column(
                        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Box(
                            Modifier.size(64.dp)
                                .background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(18.dp)),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text("H", fontSize = 30.sp, fontWeight = FontWeight.ExtraBold,
                                color = MaterialTheme.colorScheme.primary)
                        }
                        Spacer(Modifier.height(20.dp))
                        Text("正在打开完整客户端", fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(6.dp))
                        Text("画布 · 智能体工坊 · 文档工坊 · 总控中枢", fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(24.dp))
                        LinearProgressIndicator(
                            Modifier.width(160.dp).clip(RoundedCornerShape(3.dp)),
                            trackColor = MaterialTheme.colorScheme.surfaceVariant,
                        )
                    }
                }
                // V213: 主帧加载失败 → 全屏占位 + 重试（此前是白屏）
                loadError?.let { err ->
                    Column(
                        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Icon(Icons.Outlined.WifiOff, contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(40.dp))
                        Spacer(Modifier.height(14.dp))
                        Text("后端未启动 / 工作台不可达", fontSize = 17.sp, fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(6.dp))
                        Text("$err\n启动后端后点重试即可；聊天可先回「对话」页切「我的手机」直连", fontSize = 12.5.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
                        Spacer(Modifier.height(18.dp))
                        Button(onClick = { loadError = null; loading = true; webView?.reload() },
                            shape = RoundedCornerShape(12.dp)) { Text("重试", fontWeight = FontWeight.SemiBold) }
                    }
                }
            }
        }
    }
}

internal fun buildUrl(base: String, view: String = ""): String {
    val t = base.trim()
    val schemeless = t.removePrefix("https://").removePrefix("http://")
    val hostPort = schemeless.substringBefore("/")
    val hostOnly = hostPort.substringBefore(":")
    val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(hostOnly)
    val b = when {
        // IP 一律 http：IP 后端基本没有有效 TLS 证书，用 https 必然 ERR_SSL_PROTOCOL_ERROR
        isIp -> "http://$schemeless"
        t.startsWith("http://") || t.startsWith("https://") -> t
        Regex(":\\d+").containsMatchIn(hostPort) -> "http://$t"
        else -> "https://$t"
    }
    // V306 修 APP-P0-01/03：默认【不把令牌拼进 URL】——WebView 内加载靠 onPageStarted 的
    // localStorage 注入（受信 origin 才注入），令牌不再进后端 access log / WebView 历史 / Referer。
    // 仅"用系统浏览器打开"这个兜底路径（外部浏览器无法注入）才显式带令牌（includeTokens=true）。
    val params = mutableListOf<String>()
    if (view.isNotBlank()) params.add("view=${URLEncoder.encode(view, "UTF-8")}")
    if (params.isEmpty()) return b
    val sep = if (b.contains("?")) "&" else "?"
    return b + sep + params.joinToString("&")
}

@Composable
private fun ClientUrlSetup(onSave: (String) -> Unit) {
    var url by remember { mutableStateOf("") }
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Spacer(Modifier.height(24.dp))
        HashMascot(Modifier.size(120.dp))
        Spacer(Modifier.height(4.dp))
        Text("接入 HashMM 客户端", fontSize = 22.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
        Text(
            "填电脑端后端地址：公网 IP:端口（如 111.115.7.14:20014）或隧道域名都行。IP 默认走 http，域名默认 https。后端配好 HASHMM_PUBLIC_URL 会自动同步、一般无需手填。",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("客户端地址") },
            placeholder = { Text("111.115.7.14:20014 或 https://域名") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.fillMaxWidth()
        )
        Button(
            onClick = { if (url.isNotBlank()) onSave(url) },
            enabled = url.isNotBlank(),
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.fillMaxWidth().height(52.dp)
        ) { Text("保存并进入", fontWeight = FontWeight.SemiBold) }
    }
}
