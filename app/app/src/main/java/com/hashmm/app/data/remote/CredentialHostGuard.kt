package com.hashmm.app.data.remote

import okhttp3.Interceptor
import okhttp3.Response
import java.net.URI

/**
 * CredentialHostGuard — 凭证去向的纵深防御（V309）。
 *
 * ═══ 为什么要有它 ═══
 * V308 恢复了明文 HTTP（否则公网 AutoDL 直连连不上，见 network_security_config.xml），
 * 凭证安全的第一道门是 UrlSecurity.appendTokenIfSameOrigin（token 只对后端同源 URL 附加）。
 * 但那道门要求每个调用点主动去用它，而 App 里有 20+ 处手写 `.header("Authorization", ...)`。
 * 只要有一处将来忘了走同源判断、或把后端返回的外链直接拿去带 token 请求，token 就可能外发。
 *
 * 本拦截器是 OkHttp 全局层的兜底：拦下【每一个】出站请求，若它带了 Authorization 头，
 * 但目标主机不是当前登记的后端主机 → 剥掉该头再放行。正常路径下同源判断已保证不会触发；
 * 触发即说明有调用点漏网，此时「宁可这次请求不带凭证（顶多 401）」也不让 token 发去外部主机。
 *
 * host 来源：BackendHostHolder（进程内单一事实源，由 SettingsStore 在后端地址变化时更新）。
 * 判定：只比对 host（大小写不敏感）。端口/scheme 不参与——同一后端 http/https 或换端口仍是它，
 * 剥头只发生在【主机名不同】时，避免误伤后端自身的正常请求。host 未知时【保守放行】
 * （启动早期还没配后端就发的请求极少且非敏感；若要更严格可改为未知即剥头）。
 *
 * 纯逻辑（URI 解析 + 字符串比对），无 Android 依赖 → 可 JVM 单测（见 CredentialHostGuardTest）。
 */
object BackendHostHolder {
    @Volatile
    private var backendHost: String? = null

    /** 后端地址变化时调用（传完整 URL 或裸 host 均可）。解析失败则清空。 */
    fun setBackendUrl(url: String?) {
        backendHost = extractHost(url)
    }

    fun currentHost(): String? = backendHost

    /** 从任意形态的地址里取出小写 host；失败返回 null。 */
    fun extractHost(url: String?): String? {
        val s = url?.trim().orEmpty()
        if (s.isEmpty()) return null
        return try {
            val withScheme = if ("://" in s) s else "https://$s"
            URI(withScheme).host?.lowercase()
        } catch (_: Exception) {
            null
        }
    }
}

/**
 * 判定「这个请求 URL 是否允许携带凭证」的纯函数（抽出便于单测）。
 * @return true = 可带凭证（目标是后端主机，或后端主机未知时的保守放行）；false = 必须剥掉凭证。
 */
object CredentialPolicy {
    fun mayCarryCredential(requestUrl: String?, backendHost: String?): Boolean {
        val target = BackendHostHolder.extractHost(requestUrl) ?: return false // 解析不出目标 → 剥
        val backend = backendHost?.lowercase()?.takeIf { it.isNotBlank() } ?: return true // 后端未知 → 保守放行
        return target == backend
    }
}

class CredentialHostGuardInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val req = chain.request()
        val hasAuth = req.header("Authorization") != null
        if (!hasAuth) return chain.proceed(req)

        val allowed = CredentialPolicy.mayCarryCredential(
            req.url.toString(), BackendHostHolder.currentHost()
        )
        if (allowed) return chain.proceed(req)

        // 目标不是后端主机 → 剥掉凭证头再发（纵深防御：顶多 401，绝不外泄 token）
        val stripped = req.newBuilder().removeHeader("Authorization").build()
        return chain.proceed(stripped)
    }
}
