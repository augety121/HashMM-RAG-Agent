package com.hashmm.app.data.remote

import kotlinx.coroutines.delay

/**
 * RetryPolicy — 幂等只读请求的自动重试（V306）。
 *
 * 配合 HttpError 的分类：可重试码(408/429/5xx)与可重试网络异常(超时/连接/IO)才重试；
 * 4xx(除 408/429)、DNS、TLS 等确定性错误立即返回,不做无谓重试。指数退避带上限。
 *
 * 只用于**幂等**请求(GET/HEAD/幂等查询)——非幂等写请求不应自动重试(可能重复副作用)。
 * withRetry 用 Result 承载每次结果,可用脚本化 block 单测,无需真实服务器。
 */
object RetryPolicy {

    const val DEFAULT_MAX_RETRIES = 2

    /** HTTP 状态码级失败(非异常),让 withRetry 据码决定是否重试。 */
    class RetryableHttp(val code: Int, message: String = "http $code") : Exception(message)

    /** 给定第 attempt 次(从 1 起)的状态码,是否应重试。 */
    fun shouldRetryCode(code: Int, attempt: Int, maxRetries: Int = DEFAULT_MAX_RETRIES): Boolean =
        attempt <= maxRetries && HttpError.isRetryableCode(code)

    /** 给定异常,是否应重试。 */
    fun shouldRetryException(e: Throwable, attempt: Int, maxRetries: Int = DEFAULT_MAX_RETRIES): Boolean =
        attempt <= maxRetries && HttpError.fromException(e).retryable

    /** 指数退避毫秒(带 5s 上限)。 */
    fun backoffMillis(attempt: Int, baseMillis: Long = 300): Long =
        (baseMillis * (1L shl (attempt - 1).coerceIn(0, 20))).coerceAtMost(5000L)

    /**
     * 执行 block(attempt),遇可重试失败自动退避重试。
     * block 返回 Result.success 即成功返回;Result.failure(RetryableHttp) 表示 HTTP 码失败、
     * Result.failure(其它异常) 表示网络异常——是否重试由分类决定。
     */
    suspend fun <T> withRetry(
        maxRetries: Int = DEFAULT_MAX_RETRIES,
        block: suspend (attempt: Int) -> Result<T>,
    ): Result<T> {
        var attempt = 0
        var last: Result<T> = Result.failure(IllegalStateException("no attempt"))
        while (attempt < maxRetries + 1) {
            attempt++
            last = try { block(attempt) } catch (e: Throwable) { Result.failure(e) }
            if (last.isSuccess) return last
            val e = last.exceptionOrNull()
            val retry = when (e) {
                is RetryableHttp -> shouldRetryCode(e.code, attempt, maxRetries)
                null -> false
                else -> shouldRetryException(e, attempt, maxRetries)
            }
            if (!retry) return last
            delay(backoffMillis(attempt))
        }
        return last
    }
}
