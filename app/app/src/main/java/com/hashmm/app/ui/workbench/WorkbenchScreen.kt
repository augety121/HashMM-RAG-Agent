package com.hashmm.app.ui.workbench

import android.annotation.SuppressLint
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
    viewModel: WorkbenchViewModel = hiltViewModel()
) {
    val clientUrl by viewModel.clientUrl.collectAsStateWithLifecycle()
    var webView by remember { mutableStateOf<WebView?>(null) }
    var loading by remember { mutableStateOf(true) }

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
        }

        if (clientUrl.isBlank()) {
            ClientUrlSetup(onSave = viewModel::saveUrl)
        } else {
            Box(Modifier.fillMaxSize()) {
                AndroidView(
                    modifier = Modifier.fillMaxSize(),
                    factory = { ctx ->
                        WebView(ctx).apply {
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.databaseEnabled = true
                            // 桌面端 UI 在手机上缩放适配（否则按桌面宽度渲染、可视区可能空白）+ 允许缩放/混合内容
                            settings.useWideViewPort = true
                            settings.loadWithOverviewMode = true
                            settings.setSupportZoom(true)
                            settings.builtInZoomControls = true
                            settings.displayZoomControls = false
                            settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                            val token = viewModel.accessToken().orEmpty()
                            val refresh = viewModel.refreshToken().orEmpty()
                            webViewClient = object : WebViewClient() {
                                override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                                    super.onPageStarted(view, url, favicon)
                                    loading = true
                                    if (token.isNotBlank()) {
                                        val safe = token.replace("'", "\\'")
                                        val safeR = refresh.replace("'", "\\'")
                                        // 注入 access + refresh，客户端才能自己续期，不会几秒后掉登录
                                        view?.evaluateJavascript(
                                            "try{localStorage.setItem('hmm_token','$safe');if('$safeR')localStorage.setItem('hmm_refresh','$safeR');}catch(e){}", null
                                        )
                                    }
                                }
                                override fun onPageFinished(view: WebView?, url: String?) {
                                    super.onPageFinished(view, url)
                                    loading = false
                                }
                            }
                            webView = this
                            loadUrl(buildUrl(clientUrl, token, refresh))
                        }
                    },
                    update = { }
                )
                if (loading) {
                    CircularProgressIndicator(Modifier.align(Alignment.Center))
                }
            }
        }
    }
}

private fun buildUrl(base: String, token: String, refresh: String = ""): String {
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
    if (token.isBlank()) return b
    val sep = if (b.contains("?")) "&" else "?"
    var u = "$b${sep}sb_token=${URLEncoder.encode(token, "UTF-8")}"
    if (refresh.isNotBlank()) u += "&sb_refresh=${URLEncoder.encode(refresh, "UTF-8")}"
    return u
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
        HashMascot(Modifier.size(108.dp))
        Spacer(Modifier.height(4.dp))
        Text("接入 HashMM 客户端", fontSize = 22.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
        Text(
            "填电脑端后端地址：公网 IP:端口（如 ）或隧道域名都行。IP 默认走 http，域名默认 https。后端配好 HASHMM_PUBLIC_URL 会自动同步、一般无需手填。",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("客户端地址") },
            placeholder = { Text(" 或 https://域名") },
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
