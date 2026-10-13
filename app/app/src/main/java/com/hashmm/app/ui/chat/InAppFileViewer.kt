package com.hashmm.app.ui.chat

import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.webkit.URLUtil
import android.webkit.WebResourceRequest   // V308: 导航白名单需要
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

internal fun officeMimeForFilename(filename: String): String? = when (filename.substringAfterLast('.', "").lowercase()) {
    "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    "pptx" -> "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else -> null
}

internal fun officeFilenameFromUrl(url: String): String? {
    val segments = runCatching {
        java.net.URI(url).path.split('/').filter { it.isNotBlank() }
    }.getOrDefault(emptyList())
    return segments.asReversed().firstOrNull { officeMimeForFilename(it) != null }
}

private suspend fun downloadAndOpenOffice(context: Context, url: String, filename: String): String? {
    val mime = officeMimeForFilename(filename) ?: return "不支持的办公文件格式"
    val manager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
    val request = DownloadManager.Request(Uri.parse(url))
        .setTitle(filename)
        .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
        .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
    val id = runCatching { manager.enqueue(request) }.getOrElse { return "文件下载启动失败" }
    val downloadedUri = withContext(Dispatchers.IO) {
        repeat(120) {
            manager.query(DownloadManager.Query().setFilterById(id))?.use { cursor ->
                if (cursor.moveToFirst()) {
                    when (cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))) {
                        DownloadManager.STATUS_SUCCESSFUL -> return@withContext manager.getUriForDownloadedFile(id)
                        DownloadManager.STATUS_FAILED -> return@withContext null
                    }
                }
            }
            delay(500)
        }
        null
    } ?: return "文件下载失败或超时"
    val intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(downloadedUri, mime)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
    }
    return runCatching {
        context.startActivity(Intent.createChooser(intent, "选择办公应用"))
        null
    }.getOrElse { "没有可打开此文件的本机办公应用" }
}

/** 点文件卡片时调用：在 App 内打开该文件（filename, downloadUrl）。默认空实现（由会话屏提供真正打开逻辑）。 */
val LocalFileOpener = staticCompositionLocalOf<(String, String) -> Unit> { { _, _ -> } }

/**
 * App 内置文件查看器：全屏 WebView 加载后端的 /view 页（Word→HTML、Excel→表、PPT→分页、txt→文本、
 * PDF→pdf.js、图片→<img>），不跳浏览器。需要下载时落到手机下载目录（像微信"保存到本地"）。
 */
@Composable
fun InAppFileViewer(url: String, onClose: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val officeFilename = officeFilenameFromUrl(url)
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize()) {
                Row(
                    Modifier.fillMaxWidth().statusBarsPadding().padding(start = 4.dp, end = 12.dp, top = 4.dp, bottom = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, contentDescription = "关闭") }
                    Text("文件预览", fontWeight = FontWeight.SemiBold, fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurface)
                    if (officeFilename != null) {
                        TextButton(
                            onClick = {
                                scope.launch {
                                    val error = downloadAndOpenOffice(context, url, officeFilename)
                                    Toast.makeText(
                                        context,
                                        error ?: "已交给本机办公应用打开",
                                        Toast.LENGTH_SHORT,
                                    ).show()
                                }
                            },
                        ) { Text("本机应用") }
                    }
                }
                Box(Modifier.fillMaxSize().navigationBarsPadding()) {
                    AndroidView(
                        factory = { ctx ->
                            WebView(ctx).apply {
                                settings.javaScriptEnabled = true
                                settings.loadWithOverviewMode = true
                                settings.useWideViewPort = true
                                settings.builtInZoomControls = true
                                settings.displayZoomControls = false
                                settings.domStorageEnabled = true
                                // ── V308 修 P0（WebView 信任边界 / 凭证泄露）──
                                // 本 WebView 加载的 URL 里【带着登录 token】。此前：
                                //   · mixedContentMode = COMPATIBILITY_MODE（HTTPS 页里允许拉 HTTP 子资源）；
                                //   · 用默认 WebViewClient（不拦导航）——页面里任意链接/重定向都能把这个
                                //     带 token 的会话导航到【外部域名】；
                                //   · file/content 访问未显式关闭；
                                //   · 下载直接用页面给的 URL（可指向外部主机）。
                                // 合起来就是：只要预览内容被污染，token 就能被带去攻击者的域名。
                                settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                                settings.allowFileAccess = false
                                settings.allowContentAccess = false
                                @Suppress("DEPRECATION")
                                settings.allowFileAccessFromFileURLs = false
                                @Suppress("DEPRECATION")
                                settings.allowUniversalAccessFromFileURLs = false
                                settings.setGeolocationEnabled(false)
                                settings.javaScriptCanOpenWindowsAutomatically = false
                                settings.setSupportMultipleWindows(false)

                                // 导航白名单：只允许停留在【与预览地址同源】的页面。
                                // 其它一律拒绝加载（交给系统浏览器打开，绝不带着 token 跳走）。
                                webViewClient = object : WebViewClient() {
                                    override fun shouldOverrideUrlLoading(
                                        view: WebView?, request: WebResourceRequest?
                                    ): Boolean {
                                        val target = request?.url?.toString() ?: return true
                                        if (com.hashmm.app.data.settings.UrlSecurity.isSameOrigin(target, url)) return false   // 同源：放行
                                        // 非同源：不在本 WebView 打开；用系统浏览器（无 token）
                                        return try {
                                            ctx.startActivity(
                                                android.content.Intent(
                                                    android.content.Intent.ACTION_VIEW,
                                                    Uri.parse(target)
                                                )
                                            )
                                            true
                                        } catch (_: Exception) {
                                            true   // 打不开也不在本 WebView 加载
                                        }
                                    }
                                }

                                setDownloadListener { u, _, _, _, _ ->
                                    try {
                                        // 只允许下载【同源】地址：页面若被污染返回外部下载 URL，
                                        // 下面的 DownloadManager 会连同 URL 里的 token 一起发过去。
                                        if (!com.hashmm.app.data.settings.UrlSecurity.isSameOrigin(u, url)) {
                                            Toast.makeText(ctx, "已阻止下载外部地址（安全保护）", Toast.LENGTH_SHORT).show()
                                            return@setDownloadListener
                                        }
                                        val name = URLUtil.guessFileName(u, null, null)
                                        val req = DownloadManager.Request(Uri.parse(u))
                                            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                                            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
                                        (ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
                                        Toast.makeText(ctx, "开始下载：$name", Toast.LENGTH_SHORT).show()
                                    } catch (_: Exception) {
                                        Toast.makeText(ctx, "下载失败", Toast.LENGTH_SHORT).show()
                                    }
                                }
                                loadUrl(url)
                            }
                        },
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
        }
    }
}
