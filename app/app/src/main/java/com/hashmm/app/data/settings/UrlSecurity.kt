package com.hashmm.app.data.settings

/**
 * UrlSecurity — 同源判定与凭证安全的单一事实源（V308，修 APP-P0 token 外泄 / WebView 信任边界）。
 *
 * 背景：App 此前有两个凭证泄露面：
 *   1. ChatLiveRepository.fileViewUrl —— 对【任何】以 http 开头的绝对地址都追加登录 token，
 *      不校验它是否属于已配置的后端。服务端一旦返回攻击者控制的下载地址，App 就主动把
 *      用户 token 发过去。
 *   2. InAppFileViewer / CanvasScreen 的 WebView —— 用默认 WebViewClient（不拦导航）加载
 *      【带 token 的 URL】，页面里的链接/重定向/下载都能把这个会话带去外部域名。
 *
 * 这两处需要同一套"是否同源"的判断。抽到这里做单一事实源，纯 JVM 逻辑、无 Android 依赖，
 * 可被 JVM 单测充分覆盖（见 UrlSecurityTest）。
 *
 * 判定规则：scheme + host + port 三者全部相同才算同源（与浏览器同源策略一致）。
 * 任何解析失败一律返回 false（fail-closed）——宁可不加 token / 不放行导航，也不冒泄露风险。
 */
object UrlSecurity {

    /** 默认端口：https=443，http=80。 */
    private fun effectivePort(uri: java.net.URI): Int =
        if (uri.port != -1) uri.port
        else if (uri.scheme.equals("https", ignoreCase = true)) 443 else 80

    /**
     * [url] 是否与 [base] 同源（scheme + host + port 全等）。
     * 解析失败、host 为空、任一为空串 → false（fail-closed）。
     */
    fun isSameOrigin(url: String?, base: String?): Boolean {
        val u = url?.trim().orEmpty()
        val b = base?.trim().orEmpty()
        if (u.isEmpty() || b.isEmpty()) return false
        return try {
            val a = java.net.URI(u)
            val c = java.net.URI(b)
            val aHost = a.host?.lowercase() ?: return false
            val cHost = c.host?.lowercase() ?: return false
            val aScheme = a.scheme?.lowercase() ?: return false
            val cScheme = c.scheme?.lowercase() ?: return false
            aHost == cHost && aScheme == cScheme && effectivePort(a) == effectivePort(c)
        } catch (_: Exception) {
            false
        }
    }

    /**
     * 仅当 [url] 与 [base] 同源时，才为其追加 token 查询参数；否则原样返回（不带凭证）。
     * [token] 需已 URL 编码。已含 token= 的地址原样返回（避免重复追加）。
     */
    fun appendTokenIfSameOrigin(url: String, base: String, encodedToken: String): String {
        if (url.contains("token=")) return url
        if (!isSameOrigin(url, base)) return url          // 外部域名：绝不附带 token
        val sep = if (url.contains("?")) "&" else "?"
        return "$url${sep}token=$encodedToken"
    }
}
