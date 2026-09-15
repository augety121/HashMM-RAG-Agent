package com.hashmm.app.ui.canvas

import android.annotation.SuppressLint
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest   // V308: 导航白名单
import android.webkit.WebSettings          // V308: 混合内容策略
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.hashmm.app.data.remote.CanvasRepository
import com.hashmm.app.ui.theme.BrandRed
import kotlinx.coroutines.launch

/** 桌面端画布工具条同款五色 accent。 */
private val CanvasAccents = listOf("#2563eb", "#7c3aed", "#059669", "#d97706", "#e11d48")

/**
 * App 桥（与桌面 canvasBridge 同一协议心智，改走 JavascriptInterface 回传保存）：
 *   __hmm.theme(accent, fontPx)  → --accent/--wc-accent 变量 + html/body 双写字号
 *   __hmm.edit(on)               → body contentEditable + 虚线外框 + 输入防抖标脏
 *   __hmm.flush()                → HmmCanvas.onSave(整页 outerHTML)
 * 自守卫（__hmmApp），对官方画布/AI 画布一律生效。
 */
private const val APP_BRIDGE_JS = """
<script>(function(){
if (window.__hmmApp) return; window.__hmmApp = 1;
var root = document.documentElement;
window.__hmm = {
  theme: function(accent, fontPx){
    try{
      if (accent) { root.style.setProperty('--accent', accent); root.style.setProperty('--wc-accent', accent); }
      if (fontPx) { root.style.fontSize = fontPx + 'px'; if (document.body) document.body.style.fontSize = fontPx + 'px'; }
    }catch(e){}
  },
  edit: function(on){
    try{
      if (!document.body) return;
      document.body.contentEditable = on ? 'true' : 'false';
      document.body.style.outline = on ? '1.5px dashed rgba(26,26,26,0.35)' : '';
      document.body.style.outlineOffset = on ? '-1.5px' : '';
    }catch(e){}
  },
  flush: function(){
    try{ if (window.HmmCanvas) HmmCanvas.onSave('<!DOCTYPE html>\n' + document.documentElement.outerHTML); }catch(e){}
  }
};
document.addEventListener('input', function(){
  try{ if (document.body && document.body.contentEditable === 'true' && window.HmmCanvas) HmmCanvas.onDirty(); }catch(e){}
}, true);
})();</script>
"""

private fun injectBridge(html: String): String {
    if (html.contains("__hmmApp")) return html
    return if (html.contains("</body>")) html.replace("</body>", APP_BRIDGE_JS + "</body>")
    else html + APP_BRIDGE_JS
}

/**
 * 工作画布（App V248）：桌面端画布体验的手机适配——所见预览 + 主色/字号画布级覆盖 +
 * 就地编辑落盘（与桌面同一后端文件，改完桌面端立刻能看到）。
 * 全屏 Dialog 覆层，风格同全 App：白卡工具条、墨黑选中、围巾红只做强调。
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun CanvasScreen(
    convId: String,
    filename: String,
    downloadUrl: String,
    repo: CanvasRepository,
    onClose: () -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    val scope = rememberCoroutineScope()
    var html by remember { mutableStateOf<String?>(null) }
    var baseUrl by remember { mutableStateOf<String?>(null) }
    var loadErr by remember { mutableStateOf("") }
    var accent by remember { mutableStateOf(CanvasAccents[0]) }
    var fontPx by remember { mutableStateOf(14) }
    var editing by remember { mutableStateOf(false) }
    var dirty by remember { mutableStateOf(false) }
    var saveTick by remember { mutableStateOf("") }   // "" | saving | saved | 错误文案
    val webRef = remember { arrayOfNulls<WebView>(1) }

    LaunchedEffect(downloadUrl) {
        val (h, b, err) = repo.load(downloadUrl)
        loadErr = err
        baseUrl = b.ifBlank { null }
        html = if (err.isBlank()) injectBridge(h) else ""
    }

    fun js(code: String) { webRef[0]?.evaluateJavascript(code, null) }

    fun doSave(content: String) {
        scope.launch {
            saveTick = "saving"
            val err = repo.save(convId, filename, content)
            saveTick = if (err.isBlank()) "saved" else err
            if (err.isBlank()) {
                dirty = false
                kotlinx.coroutines.delay(1600)
                if (saveTick == "saved") saveTick = ""
            }
        }
    }

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = cs.background) {
            Column(Modifier.fillMaxSize()) {
                // ── 页头 ──
                Row(
                    Modifier.fillMaxWidth().statusBarsPadding()
                        .padding(start = 4.dp, end = 14.dp, top = 4.dp, bottom = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, contentDescription = "关闭") }
                    Column(Modifier.weight(1f)) {
                        Text("工作画布", fontWeight = FontWeight.Bold, fontSize = 16.5.sp,
                            color = cs.onSurface, letterSpacing = (-0.3).sp)
                        Text(filename, fontSize = 11.sp, color = cs.onSurfaceVariant, maxLines = 1)
                    }
                    when {
                        saveTick == "saving" -> CircularProgressIndicator(Modifier.size(15.dp), strokeWidth = 1.8.dp, color = cs.onSurfaceVariant)
                        saveTick == "saved" -> Text("已保存 ✓", fontSize = 11.5.sp, fontWeight = FontWeight.Medium, color = Color(0xFF15803D))
                        saveTick.isNotBlank() -> Text(saveTick, fontSize = 11.sp, color = BrandRed, maxLines = 1)
                    }
                }

                // ── 工具条：主色五点 · 字号 · 编辑 · 保存（与桌面画布同一心智，Marvis 面貌）──
                Surface(color = cs.surface, shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp)) {
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                            .padding(horizontal = 13.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("主色", fontSize = 11.sp, color = cs.onSurfaceVariant)
                        Spacer(Modifier.width(8.dp))
                        CanvasAccents.forEach { c ->
                            val sel = accent == c
                            Box(
                                Modifier.padding(end = 7.dp).size(20.dp).clip(CircleShape)
                                    .background(Color(android.graphics.Color.parseColor(c)))
                                    .then(if (sel) Modifier.border(2.dp, cs.onSurface, CircleShape) else Modifier)
                                    .clickable { accent = c; js("__hmm.theme('$c', 0)") },
                            )
                        }
                        Box(Modifier.padding(horizontal = 6.dp).width(0.5.dp).height(18.dp)
                            .background(cs.outlineVariant.copy(alpha = 0.7f)))
                        Text("字号", fontSize = 11.sp, color = cs.onSurfaceVariant)
                        Spacer(Modifier.width(6.dp))
                        ToolPill("A−") { if (fontPx > 11) { fontPx--; js("__hmm.theme('', $fontPx)") } }
                        Text("$fontPx", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold,
                            color = cs.onSurface, modifier = Modifier.padding(horizontal = 7.dp))
                        ToolPill("A＋") { if (fontPx < 20) { fontPx++; js("__hmm.theme('', $fontPx)") } }
                        Box(Modifier.padding(horizontal = 6.dp).width(0.5.dp).height(18.dp)
                            .background(cs.outlineVariant.copy(alpha = 0.7f)))
                        // 编辑 chip：选中＝墨黑实心白字
                        Surface(
                            color = if (editing) cs.primary else cs.surfaceVariant.copy(alpha = 0.6f),
                            shape = RoundedCornerShape(50),
                            modifier = Modifier.clip(RoundedCornerShape(50)).clickable {
                                editing = !editing
                                js("__hmm.edit(${editing})")
                                if (!editing && dirty) js("__hmm.flush()")
                            },
                        ) {
                            Text(if (editing) "编辑中" else "编辑", fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = if (editing) cs.onPrimary else cs.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 13.dp, vertical = 6.dp))
                        }
                        if (dirty) {
                            Spacer(Modifier.width(8.dp))
                            Surface(color = cs.primary, shape = RoundedCornerShape(50),
                                modifier = Modifier.clip(RoundedCornerShape(50)).clickable { js("__hmm.flush()") }) {
                                Text("保存", fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                                    color = cs.onPrimary,
                                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp))
                            }
                        }
                    }
                }
                Spacer(Modifier.height(10.dp))

                // ── 画布 ──
                Box(Modifier.fillMaxSize().padding(horizontal = 14.dp).navigationBarsPadding()) {
                    when {
                        loadErr.isNotBlank() -> Text(loadErr, fontSize = 13.sp, color = cs.onSurfaceVariant,
                            modifier = Modifier.align(Alignment.Center))
                        html == null -> CircularProgressIndicator(
                            Modifier.align(Alignment.Center).size(26.dp), strokeWidth = 2.5.dp, color = cs.onSurfaceVariant)
                        else -> Surface(color = Color.White, shape = RoundedCornerShape(16.dp),
                            modifier = Modifier.fillMaxSize()) {
                            AndroidView(
                                factory = { ctx ->
                                    WebView(ctx).apply {
                                        settings.javaScriptEnabled = true
                                        settings.domStorageEnabled = true
                                        settings.loadWithOverviewMode = true
                                        settings.useWideViewPort = true
                                        // ── V308 修 P1（WebView 信任边界）──
                                        // 本 WebView 用 loadDataWithBaseURL 加载【后端返回的 HTML】，
                                        // 并通过 addJavascriptInterface 暴露原生桥 HmmCanvas。此前用
                                        // 默认 WebViewClient（不拦导航）、未关 file/content 访问、未限
                                        // 混合内容 —— 一旦后端 HTML 被污染，页面脚本可直接调用暴露的
                                        // 原生方法，并把会话导航到任意外部页面。这里收口：
                                        settings.allowFileAccess = false
                                        settings.allowContentAccess = false
                                        @Suppress("DEPRECATION")
                                        settings.allowFileAccessFromFileURLs = false
                                        @Suppress("DEPRECATION")
                                        settings.allowUniversalAccessFromFileURLs = false
                                        settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                                        settings.setGeolocationEnabled(false)
                                        settings.javaScriptCanOpenWindowsAutomatically = false
                                        settings.setSupportMultipleWindows(false)

                                        // 导航白名单：Canvas 只应停留在它自己的内容里。
                                        // 任何跳转（含被注入 HTML 触发的）一律不在本 WebView 打开，
                                        // 外链交系统浏览器 —— 原生桥所在的页面绝不被替换成外部页面。
                                        webViewClient = object : WebViewClient() {
                                            override fun shouldOverrideUrlLoading(
                                                view: WebView?, request: WebResourceRequest?
                                            ): Boolean {
                                                val target = request?.url?.toString() ?: return true
                                                if (com.hashmm.app.data.settings.UrlSecurity
                                                        .isSameOrigin(target, baseUrl)) return false
                                                return try {
                                                    ctx.startActivity(
                                                        android.content.Intent(
                                                            android.content.Intent.ACTION_VIEW,
                                                            android.net.Uri.parse(target)
                                                        )
                                                    )
                                                    true
                                                } catch (_: Exception) { true }
                                            }
                                        }
                                        addJavascriptInterface(object {
                                            @JavascriptInterface
                                            fun onSave(content: String) { doSave(content) }
                                            @JavascriptInterface
                                            fun onDirty() { dirty = true }
                                        }, "HmmCanvas")
                                        webRef[0] = this
                                        loadDataWithBaseURL(baseUrl, html ?: "", "text/html", "utf-8", null)
                                    }
                                },
                                modifier = Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ToolPill(label: String, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surfaceVariant.copy(alpha = 0.6f), shape = RoundedCornerShape(9.dp),
        modifier = Modifier.clip(RoundedCornerShape(9.dp)).clickable(onClick = onClick)) {
        Text(label, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = cs.onSurface,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp))
    }
}

/** 仅用于经 Hilt 拿到 CanvasRepository（覆层式 CanvasScreen 无导航参数）。 */
@dagger.hilt.android.lifecycle.HiltViewModel
class CanvasViewModel @javax.inject.Inject constructor(
    val repo: CanvasRepository,
) : androidx.lifecycle.ViewModel()
