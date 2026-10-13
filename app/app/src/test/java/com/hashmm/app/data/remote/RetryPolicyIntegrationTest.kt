package com.hashmm.app.data.remote

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * V306 网络层集成测试(真实 HTTP 往返,MockWebServer;`./gradlew test` 可跑)。
 *
 * 用真实 OkHttp 打真实(本地 mock)服务器,验证 RetryPolicy 在超时/5xx/4xx 下的真实行为:
 *   · 5xx→5xx→200：自动重试并最终成功(服务器真收到 3 次请求)；
 *   · 4xx：不重试(只 1 次请求)；
 *   · 超时：可重试(重试到成功或耗尽)；
 * 与纯逻辑单测互补——这里走真实 socket/连接池/超时。
 */
class RetryPolicyIntegrationTest {

    private lateinit var server: MockWebServer
    private lateinit var client: OkHttpClient

    @Before fun setUp() {
        server = MockWebServer()
        server.start()
        client = OkHttpClient.Builder()
            .connectTimeout(2, TimeUnit.SECONDS)
            .readTimeout(1, TimeUnit.SECONDS)
            .build()
    }

    @After fun tearDown() {
        server.shutdown()
    }

    private suspend fun getWithRetry(path: String, maxRetries: Int = 2): Result<String> =
        RetryPolicy.withRetry(maxRetries) { _ ->
            try {
                val req = Request.Builder().url(server.url(path)).get().build()
                client.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        Result.failure(RetryPolicy.RetryableHttp(resp.code))
                    } else {
                        Result.success(resp.body?.string() ?: "")
                    }
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    @Test fun retriesOn5xxThenSucceeds() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(500))
        server.enqueue(MockResponse().setResponseCode(200).setBody("OK-BODY"))
        val r = getWithRetry("/api/x")
        assertTrue("最终应成功", r.isSuccess)
        assertEquals("OK-BODY", r.getOrNull())
        assertEquals("服务器应真实收到 3 次请求(2 次重试)", 3, server.requestCount)
    }

    @Test fun doesNotRetryOn4xx() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404))
        val r = getWithRetry("/api/x")
        assertTrue(r.isFailure)
        assertEquals("4xx 不该重试,只 1 次请求", 1, server.requestCount)
    }

    @Test fun retriesOnTimeoutThenSucceeds() = runBlocking {
        // 第一次:服务器不回(触发 readTimeout);第二次:正常
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        server.enqueue(MockResponse().setResponseCode(200).setBody("RECOVERED"))
        val r = getWithRetry("/api/x", maxRetries = 2)
        assertTrue("超时后重试应成功", r.isSuccess)
        assertEquals("RECOVERED", r.getOrNull())
        assertTrue("至少 2 次请求", server.requestCount >= 2)
    }

    @Test fun exhaustsOnPersistent5xx() = runBlocking {
        repeat(5) { server.enqueue(MockResponse().setResponseCode(503)) }
        val r = getWithRetry("/api/x", maxRetries = 2)
        assertTrue(r.isFailure)
        assertEquals("maxRetries+1 = 3 次请求后放弃", 3, server.requestCount)
    }
}
