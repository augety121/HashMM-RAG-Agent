package com.hashmm.app.data.remote

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * V306 重试策略单元测试(纯 JVM,用脚本化 block,无需真实服务器;`./gradlew test` 可跑)。
 * 用极小 backoff(base=1ms)避免测试变慢。
 */
class RetryPolicyTest {

    @Test fun shouldRetryCode_retryableOnly() {
        assertTrue(RetryPolicy.shouldRetryCode(503, 1, 2))
        assertTrue(RetryPolicy.shouldRetryCode(429, 1, 2))
        assertTrue(RetryPolicy.shouldRetryCode(408, 1, 2))
        assertFalse("404 不该重试", RetryPolicy.shouldRetryCode(404, 1, 2))
        assertFalse("超次数不该重试", RetryPolicy.shouldRetryCode(503, 3, 2))
    }

    @Test fun shouldRetryException_networkOnly() {
        assertTrue(RetryPolicy.shouldRetryException(java.net.SocketTimeoutException(), 1, 2))
        assertTrue(RetryPolicy.shouldRetryException(java.net.ConnectException(), 1, 2))
        assertFalse("DNS 不该重试", RetryPolicy.shouldRetryException(java.net.UnknownHostException(), 1, 2))
        assertFalse("TLS 不该重试", RetryPolicy.shouldRetryException(javax.net.ssl.SSLException("x"), 1, 2))
    }

    @Test fun backoff_growsAndCaps() {
        assertTrue(RetryPolicy.backoffMillis(1) <= RetryPolicy.backoffMillis(2))
        assertEquals(5000L, RetryPolicy.backoffMillis(30))   // 上限
    }

    @Test fun withRetry_successFirstAttempt() = runBlocking {
        var calls = 0
        val r = RetryPolicy.withRetry(2) { calls++; Result.success(42) }
        assertEquals(42, r.getOrNull())
        assertEquals(1, calls)
    }

    @Test fun withRetry_5xxThenSuccess() = runBlocking {
        var calls = 0
        val r = RetryPolicy.withRetry(2, block = { attempt ->
            calls++
            if (attempt == 1) Result.failure(RetryPolicy.RetryableHttp(503)) else Result.success("done")
        })
        assertEquals("done", r.getOrNull())
        assertEquals(2, calls)
    }

    @Test fun withRetry_5xxAlways_exhausts() = runBlocking {
        var calls = 0
        val r = RetryPolicy.withRetry<String>(2) { calls++; Result.failure(RetryPolicy.RetryableHttp(500)) }
        assertTrue(r.isFailure)
        assertEquals(3, calls)   // maxRetries + 1
    }

    @Test fun withRetry_404_noRetry() = runBlocking {
        var calls = 0
        val r = RetryPolicy.withRetry<String>(2) { calls++; Result.failure(RetryPolicy.RetryableHttp(404)) }
        assertTrue(r.isFailure)
        assertEquals(1, calls)
    }

    @Test fun withRetry_timeoutThenSuccess() = runBlocking {
        var calls = 0
        val r = RetryPolicy.withRetry(2, block = { attempt ->
            calls++
            if (attempt < 3) Result.failure(java.net.SocketTimeoutException()) else Result.success("ok3")
        })
        assertEquals("ok3", r.getOrNull())
        assertEquals(3, calls)
    }

    @Test fun withRetry_dnsException_noRetry() = runBlocking {
        var calls = 0
        val r = RetryPolicy.withRetry<String>(2) { calls++; Result.failure(java.net.UnknownHostException()) }
        assertTrue(r.isFailure)
        assertEquals(1, calls)
    }
}
