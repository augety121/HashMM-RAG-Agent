package com.hashmm.app.data.remote

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * V308 重写：**真正测 LiveChatManager 生产类**。
 *
 * 原 LiveChatManagerTest 是审计点名的"假测试"——整个文件从未实例化或调用过 LiveChatManager，
 * 只在断言 StringBuilder 和普通协程。它对下面两个【真实竞态】完全发现不了：
 *
 *   1. **同会话重复发送**：原 stream() 是「先查 jobs[convId] → 再 async → 再写回 jobs」的
 *      非原子 check-then-act。两个并发调用可能同时发现任务不存在，于是对同一会话发出
 *      两次后端请求（重复计费、重复落库、两条流互相覆盖）。
 *   2. **状态互相覆盖**：put/update/remove 原本是对 _streams.value 的普通读—改—写。
 *      不同会话在 IO 线程并发更新时，后写者用自己读到的旧 Map 覆盖先写者的更新，
 *      某个会话的流式状态会凭空消失。
 *
 * 现在 LiveChatManager 依赖 LiveStreamSource 接口（V308 可测性重构），这里注入纯内存 Fake，
 * 直接对上述契约下断言。这些测试在修复前【会失败】，是真正的回归护栏。
 */
class LiveChatManagerTest {

    /** 纯内存 Fake：记录每个会话被真正请求了几次。 */
    private class FakeSource(
        private val tokens: List<String> = listOf("你好", "，", "世界"),
        private val delayPerTokenMs: Long = 5,
        private val result: Boolean = true,
    ) : LiveStreamSource {
        val totalCalls = AtomicInteger(0)
        val callsPerConv = ConcurrentHashMap<String, AtomicInteger>()

        override suspend fun streamMessage(
            convId: String,
            message: String,
            onToken: (String) -> Unit,
        ): Boolean {
            totalCalls.incrementAndGet()
            callsPerConv.computeIfAbsent(convId) { AtomicInteger(0) }.incrementAndGet()
            for (t in tokens) {
                delay(delayPerTokenMs)
                onToken(t)
            }
            return result
        }
    }

    // ── 契约 1：同会话并发只发一次请求（修复前会发多次）──
    @Test
    fun concurrentSameConversation_sendsOnlyOneRequest() = runBlocking {
        val src = FakeSource(delayPerTokenMs = 20)
        val mgr = LiveChatManager(src)
        val conv = "conv-A"

        val results = withContext(Dispatchers.Default) {
            (1..10).map { async { mgr.stream(conv, "同一条消息") { } } }.awaitAll()
        }

        assertEquals(
            "同会话并发 10 次，底层只应真正请求 1 次（computeIfAbsent 原子创建）",
            1, src.callsPerConv[conv]?.get(),
        )
        assertTrue("所有调用方都应拿到结果", results.all { it })
    }

    // ── 契约 2：不同会话并发不丢状态、各发一次（修复前会互相覆盖）──
    @Test
    fun concurrentDifferentConversations_eachSentExactlyOnce() = runBlocking {
        val src = FakeSource(delayPerTokenMs = 3)
        val mgr = LiveChatManager(src)
        val convs = (1..20).map { "conv-$it" }

        withContext(Dispatchers.Default) {
            convs.map { c -> async { mgr.stream(c, "消息 $c") { } } }.awaitAll()
        }

        assertEquals("20 个会话共请求 20 次", 20, src.totalCalls.get())
        for (c in convs) {
            assertEquals("会话 $c 应恰好请求 1 次", 1, src.callsPerConv[c]?.get())
        }
    }

    // ── 契约 3：流跑完后状态清理，不泄漏 ──
    @Test
    fun afterCompletion_stateIsCleanedUp() = runBlocking {
        val src = FakeSource(delayPerTokenMs = 1)
        val mgr = LiveChatManager(src)
        mgr.stream("conv-X", "hi") { }
        assertFalse("完成后不应仍在 streaming", mgr.isStreaming("conv-X"))
        assertNull("完成后 streams 应已清理该会话", mgr.liveFor("conv-X"))
    }

    // ── 契约 4：回吐的是【累计】内容且单调增长（气泡永不回退）──
    @Test
    fun content_isAccumulatedAndMonotonic() = runBlocking {
        val src = FakeSource(tokens = listOf("A", "B", "C"), delayPerTokenMs = 10)
        val mgr = LiveChatManager(src)
        val seen = mutableListOf<String>()
        mgr.stream("conv-Y", "go") { c -> seen.add(c) }

        val nonEmpty = seen.filter { it.isNotEmpty() }
        for (i in 1 until nonEmpty.size) {
            assertTrue(
                "累计内容必须单调增长: '${nonEmpty[i - 1]}' -> '${nonEmpty[i]}'",
                nonEmpty[i].startsWith(nonEmpty[i - 1]),
            )
        }
    }

    // ── 契约 5：底层失败必须如实返回 false（供上层走兜底模型，不能谎报成功）──
    @Test
    fun repositoryFailure_isPropagatedAsFalse() = runBlocking {
        val src = FakeSource(tokens = emptyList(), result = false)
        val mgr = LiveChatManager(src)
        val ok = mgr.stream("conv-Z", "hi") { }
        assertFalse("底层返回 false 时 manager 必须如实返回 false", ok)
    }

    @Test
    fun semanticEvents_areExposedWhileTaskIsRunning() = runBlocking {
        val src = object : LiveStreamSource {
            override suspend fun streamMessage(
                convId: String,
                message: String,
                onToken: (String) -> Unit,
            ): Boolean = true

            override suspend fun streamMessageWithEvents(
                convId: String,
                message: String,
                onToken: (String) -> Unit,
                onEvent: (ChatStreamEvent) -> Unit,
            ): Boolean {
                onEvent(ChatStreamEvent("task_contract", "{\"goal\":\"完成联动\"}"))
                onEvent(ChatStreamEvent("todo", "{\"items\":[{\"status\":\"completed\"}]}"))
                onEvent(ChatStreamEvent("progress", "{\"msg\":\"正在验证\"}"))
                onEvent(ChatStreamEvent("step_start", "{\"step\":1}"))
                onToken("结果")
                delay(150)
                return true
            }
        }
        val mgr = LiveChatManager(src)
        val result = async { mgr.stream("conv-semantic", "执行任务") { } }

        val snapshot = withTimeout(1_000) {
            while (true) {
                val current = mgr.liveFor("conv-semantic")
                if (current?.progress?.contains("正在验证") == true) return@withTimeout current
                delay(5)
            }
            error("unreachable")
        }
        assertTrue(snapshot.taskContract.contains("完成联动"))
        assertTrue(snapshot.todo.contains("completed"))
        assertEquals(1, snapshot.stepCount)
        assertTrue(result.await())
    }

    @Test
    fun activeTurn_canBeSteeredAndInterruptedThroughSameConversation() = runBlocking {
        val steers = mutableListOf<List<String>>()
        val interrupts = AtomicInteger(0)
        val src = object : LiveStreamSource {
            override suspend fun streamMessage(
                convId: String,
                message: String,
                onToken: (String) -> Unit,
            ): Boolean = true

            override suspend fun streamMessageWithEvents(
                convId: String,
                message: String,
                onToken: (String) -> Unit,
                onEvent: (ChatStreamEvent) -> Unit,
            ): Boolean {
                onEvent(ChatStreamEvent(
                    "turn_started",
                    "{\"turn_id\":\"turn-app\",\"steerable\":true,\"status\":\"running\"}",
                ))
                delay(300)
                onEvent(ChatStreamEvent("done", "{\"status\":\"interrupted\"}"))
                return true
            }

            override suspend fun steerTurn(
                convId: String,
                turnId: String,
                content: String,
                clientMessageId: String,
            ): SteerTurnResult {
                steers.add(listOf(convId, turnId, content, clientMessageId))
                return SteerTurnResult(true, false, "message-app")
            }

            override suspend fun interruptTurn(convId: String, turnId: String): Boolean {
                assertEquals("conv-turn", convId)
                assertEquals("turn-app", turnId)
                interrupts.incrementAndGet()
                return true
            }
        }
        val mgr = LiveChatManager(src)
        val result = async { mgr.stream("conv-turn", "初始任务") { } }

        val active = withTimeout(1_000) {
            while (true) {
                val current = mgr.liveFor("conv-turn")
                if (current?.turnSteerable == true) return@withTimeout current
                delay(5)
            }
            error("unreachable")
        }
        assertEquals("turn-app", active.turnId)
        val steered = mgr.steer("conv-turn", "增加风险清单", "client-app")
        assertTrue(steered?.accepted == true)
        assertEquals(listOf("conv-turn", "turn-app", "增加风险清单", "client-app"), steers.single())

        assertTrue(mgr.interrupt("conv-turn"))
        assertEquals(1, interrupts.get())
        assertEquals("interrupting", mgr.liveFor("conv-turn")?.turnStatus)
        assertFalse(mgr.liveFor("conv-turn")?.turnSteerable ?: true)
        assertTrue(result.await())
    }

    @Test
    fun cancel_stopsManagerAndUnderlyingTransport() = runBlocking {
        val cancelled = AtomicInteger(0)
        val src = object : LiveStreamSource {
            override suspend fun streamMessage(
                convId: String,
                message: String,
                onToken: (String) -> Unit,
            ): Boolean {
                repeat(100) {
                    delay(20)
                    onToken("x")
                }
                return true
            }

            override fun cancelStream(convId: String) {
                cancelled.incrementAndGet()
            }
        }
        val mgr = LiveChatManager(src)
        val result = async { mgr.stream("conv-cancel", "停止测试") { } }
        withTimeout(1_000) {
            while (!mgr.isStreaming("conv-cancel")) delay(5)
        }

        mgr.cancel("conv-cancel")

        assertFalse(result.await())
        assertEquals(1, cancelled.get())
        assertNull(mgr.liveFor("conv-cancel"))
    }
}
