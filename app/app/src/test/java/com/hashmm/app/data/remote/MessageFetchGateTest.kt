package com.hashmm.app.data.remote

import com.hashmm.app.data.sync.ChatMessage
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MessageFetchGateTest {
    private val snapshot = listOf(
        ChatMessage(
            id = "message-1",
            convId = "conversation-1",
            role = "assistant",
            content = "done",
        )
    )

    @Test
    fun concurrentReadsForSameConversation_shareOneLoader() = runBlocking {
        val gate = MessageFetchGate(freshForMs = 2_000L)
        val calls = AtomicInteger(0)
        val started = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()

        val results = withContext(Dispatchers.Default) {
            (1..20).map {
                async {
                    gate.fetch("conversation-1") {
                        calls.incrementAndGet()
                        started.complete(Unit)
                        release.await()
                        snapshot
                    }
                }
            }.also {
                started.await()
                release.complete(Unit)
            }.awaitAll()
        }

        assertEquals(1, calls.get())
        assertEquals(List(20) { snapshot }, results)
    }

    @Test
    fun failedReadIsBrieflyCachedToProtectRecoveringBackend() = runBlocking {
        var now = 10_000L
        val gate = MessageFetchGate(freshForMs = 2_000L, nowMs = { now })
        val calls = AtomicInteger(0)

        assertNull(gate.fetch("conversation-1") { calls.incrementAndGet(); null })
        assertNull(gate.fetch("conversation-1") { calls.incrementAndGet(); snapshot })
        assertEquals(1, calls.get())

        now += 2_001L
        assertEquals(snapshot, gate.fetch("conversation-1") { calls.incrementAndGet(); snapshot })
        assertEquals(2, calls.get())
    }

    @Test
    fun invalidationAllowsImmediateRefreshAfterAWrite() = runBlocking {
        var calls = 0
        val gate = MessageFetchGate(freshForMs = 60_000L)

        gate.fetch("conversation-1") { calls += 1; emptyList() }
        gate.invalidate("conversation-1")
        val refreshed = gate.fetch("conversation-1") { calls += 1; snapshot }

        assertEquals(2, calls)
        assertEquals(snapshot, refreshed)
    }
}
