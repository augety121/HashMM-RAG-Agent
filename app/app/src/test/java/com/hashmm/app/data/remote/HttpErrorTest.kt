package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * V306 HTTP/网络错误分类单元测试（纯 JVM；`./gradlew test` 可跑）。
 * 锁定"状态码/异常 → 文案 + 是否可重试 + 类别"的口径,防各 Repository 各写一套漂移。
 */
class HttpErrorTest {

    @Test fun code_auth_forbidden_notFound_notRetryable() {
        assertEquals("auth", HttpError.fromCode(401).kind)
        assertFalse(HttpError.fromCode(401).retryable)
        assertEquals("forbidden", HttpError.fromCode(403).kind)
        assertEquals("需要管理员权限", HttpError.fromCode(403).message)
        assertEquals("not_found", HttpError.fromCode(404).kind)
    }

    @Test fun code_429_and_5xx_retryable() {
        assertTrue("429 应可重试", HttpError.fromCode(429).retryable)
        assertEquals("rate_limited", HttpError.fromCode(429).kind)
        assertTrue("500 应可重试", HttpError.fromCode(500).retryable)
        assertTrue("503 应可重试", HttpError.fromCode(503).retryable)
        assertEquals("server", HttpError.fromCode(502).kind)
        assertTrue(HttpError.fromCode(500).message.contains("500"))
    }

    @Test fun code_generic_4xx_notRetryable() {
        assertFalse(HttpError.fromCode(400).retryable)
        assertEquals("client", HttpError.fromCode(418).kind)
    }

    @Test fun code_2xx_ok() {
        assertEquals("ok", HttpError.fromCode(200).kind)
        assertEquals("ok", HttpError.fromCode(204).kind)
    }

    @Test fun exception_timeout_retryable() {
        val i = HttpError.fromException(java.net.SocketTimeoutException("t"))
        assertTrue(i.retryable)
        assertEquals("timeout", i.kind)
    }

    @Test fun exception_dns_notRetryable() {
        val i = HttpError.fromException(java.net.UnknownHostException("h"))
        assertFalse(i.retryable)
        assertEquals("dns", i.kind)
    }

    @Test fun exception_connect_retryable() {
        assertTrue(HttpError.fromException(java.net.ConnectException("c")).retryable)
        assertEquals("connect", HttpError.fromException(java.net.ConnectException("c")).kind)
    }

    @Test fun exception_tls_notRetryable() {
        val i = HttpError.fromException(javax.net.ssl.SSLException("ssl"))
        assertFalse(i.retryable)
        assertEquals("tls", i.kind)
    }

    @Test fun exception_generic_io_retryable() {
        assertTrue(HttpError.fromException(java.io.IOException("io")).retryable)
        assertEquals("io", HttpError.fromException(java.io.IOException("io")).kind)
    }

    @Test fun isRetryableCode_helper() {
        assertTrue(HttpError.isRetryableCode(503))
        assertTrue(HttpError.isRetryableCode(429))
        assertFalse(HttpError.isRetryableCode(404))
        assertFalse(HttpError.isRetryableCode(200))
    }
}
