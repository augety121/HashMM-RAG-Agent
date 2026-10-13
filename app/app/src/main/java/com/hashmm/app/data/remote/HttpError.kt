package com.hashmm.app.data.remote

/**
 * HttpError — 统一的 HTTP/网络错误分类（V306）。
 *
 * 背景：20+ 个 Repository 各自内联判 `resp.code == 403` / `!isSuccessful` / `catch (e)`，
 * 文案与"是否可重试"口径不一、无法单测。本对象把"状态码/异常 → 用户可读文案 + 是否可重试 +
 * 错误类别"收敛成一处纯逻辑（无 Android 依赖，java.net/javax.net），可 JVM 单测。
 *
 * 用法（Repository 可逐步采用，不强制）：
 *   resp.use { if (!it.isSuccessful) return HttpError.fromCode(it.code) ... }
 *   catch (e: Exception) { return HttpError.fromException(e) }
 * retryable=true 的（超时/连接抖动/5xx/429）适合幂等只读请求自动重试一次。
 */
object HttpError {

    data class Info(val message: String, val retryable: Boolean, val kind: String)

    /** 由 HTTP 状态码分类。 */
    fun fromCode(code: Int): Info = when (code) {
        401 -> Info("登录已失效，请重新登录", false, "auth")
        403 -> Info("需要管理员权限", false, "forbidden")
        404 -> Info("请求的资源不存在", false, "not_found")
        408 -> Info("请求超时，请稍后重试", true, "timeout")
        409 -> Info("操作冲突，请刷新后重试", false, "conflict")
        413 -> Info("内容过大", false, "too_large")
        429 -> Info("请求过于频繁，请稍后再试", true, "rate_limited")
        in 500..599 -> Info("服务暂时不可用（$code），请稍后重试", true, "server")
        in 400..499 -> Info("请求有误（$code）", false, "client")
        in 200..299 -> Info("", false, "ok")
        else -> Info("请求失败（$code）", false, "unknown")
    }

    /** 由网络层抛出的异常分类。 */
    fun fromException(e: Throwable): Info = when (e) {
        is java.net.SocketTimeoutException -> Info("连接超时，请检查网络后重试", true, "timeout")
        is java.net.UnknownHostException -> Info("无法解析服务器地址，请检查地址与网络", false, "dns")
        is java.net.ConnectException -> Info("无法连接到服务器，请稍后重试", true, "connect")
        is javax.net.ssl.SSLException -> Info("安全连接失败（证书/TLS 问题）", false, "tls")
        is java.io.IOException -> Info("网络异常，请稍后重试", true, "io")
        else -> Info("发生错误：${e.message ?: e.javaClass.simpleName}", false, "error")
    }

    /** 便捷：状态码是否值得重试（幂等请求用）。 */
    fun isRetryableCode(code: Int): Boolean = fromCode(code).retryable
}
