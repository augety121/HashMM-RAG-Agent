package com.hashmm.app.data.settings

/**
 * ClientUrlValidator — 客户端/云同步服务器地址校验（V306，修 APP-P0-04）。
 *
 * 背景：此前 setClientUrl / setSyncedClientUrl 只做 trim。后续所有请求都会带 Token——
 * 若地址被错误同步、恶意配置或输入成非预期服务，Token 就被发到错误主机。
 *
 * 本校验器**不强制 HTTPS**（用户的 AutoDL 私有云常是局域网 HTTP，强制 HTTPS 会误伤正常使用），
 * 但拦掉真正危险/畸形的输入：
 *   · 必须是 http/https；
 *   · 必须有非空主机名；
 *   · 拒绝 userinfo（http://user:pass@host —— 钓鱼/凭据泄露向量）；
 *   · 拒绝空白/畸形；
 *   · 归一化：去首尾空白、去结尾斜杠。
 *
 * 纯逻辑、无 Android 依赖 → 可 JVM 单测。
 */
object ClientUrlValidator {

    /** 校验并归一化。合法返回归一化地址；非法返回 null（调用方据此拒绝写入并提示用户）。 */
    fun normalizeOrNull(raw: String?): String? {
        val s = raw?.trim().orEmpty()
        if (s.isEmpty()) return null

        // 仅接受 http/https 前缀（大小写不敏感）
        val lower = s.lowercase()
        if (!lower.startsWith("http://") && !lower.startsWith("https://")) return null

        // 用 java.net.URI 解析，拿到结构化字段做判定
        val uri = try { java.net.URI(s) } catch (e: Exception) { return null }

        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return null

        val host = uri.host
        if (host.isNullOrBlank()) return null            // 必须有主机名（挡 http:///path 之类畸形）

        // 拒绝 userinfo（user:pass@host）
        if (!uri.userInfo.isNullOrEmpty()) return null

        // 归一化：去结尾斜杠（与既有 setter 行为一致）
        return s.trimEnd('/')
    }

    /** 便捷判定：是否为可接受的地址。 */
    fun isValid(raw: String?): Boolean = normalizeOrNull(raw) != null
}
