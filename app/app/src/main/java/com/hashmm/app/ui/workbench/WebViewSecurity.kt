package com.hashmm.app.ui.workbench

/**
 * WebViewSecurity — App 内 WebView 的信任边界工具（V306，修 APP-P0-03）。
 *
 * 目的：把"只信任配置的后端 origin"这条判定收敛成一处纯逻辑，供 shouldOverrideUrlLoading
 * 的导航白名单、以及"只在受信 origin 才注入令牌"两处复用，避免各页各写一套、口径漂移。
 *
 * origin = scheme://host[:port]，比较时忽略路径/查询；scheme/host 大小写不敏感。
 * 纯逻辑、无 Android 依赖 → 可 JVM 单测。
 */
object WebViewSecurity {

    /** 从任意 URL 抽取归一化 origin（scheme://host[:port]）；无法解析返回 null。 */
    fun originOf(url: String?): String? {
        val s = url?.trim().orEmpty()
        if (s.isEmpty()) return null
        return try {
            val u = java.net.URI(s)
            val scheme = u.scheme?.lowercase() ?: return null
            if (scheme != "http" && scheme != "https") return null
            val host = u.host?.lowercase() ?: return null
            if (host.isBlank()) return null
            val port = u.port
            if (port >= 0) "$scheme://$host:$port" else "$scheme://$host"
        } catch (e: Exception) {
            null
        }
    }

    /** 目标 URL 是否与受信基址同源。二者任一无法解析出 origin → 不信任（保守）。 */
    fun isSameOrigin(targetUrl: String?, trustedBaseUrl: String?): Boolean {
        val a = originOf(targetUrl) ?: return false
        val b = originOf(trustedBaseUrl) ?: return false
        return a == b
    }
}
