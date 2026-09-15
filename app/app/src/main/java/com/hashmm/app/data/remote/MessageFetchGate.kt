package com.hashmm.app.data.remote

import com.hashmm.app.data.sync.ChatMessage
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Coalesces message snapshot reads per conversation.
 *
 * A Supabase event, a screen restore and a long-task fallback poll can arrive
 * together. Only one network call may run for that conversation; followers
 * reuse its short-lived result, including a failed result, instead of creating
 * a request storm while the backend is recovering.
 */
internal class MessageFetchGate(
    private val freshForMs: Long = 2_000L,
    private val nowMs: () -> Long = System::currentTimeMillis,
) {
    private class Entry {
        val mutex = Mutex()
        var hasSnapshot = false
        var fetchedAtMs = 0L
        var snapshot: List<ChatMessage>? = null
    }

    private val entries = ConcurrentHashMap<String, Entry>()

    suspend fun fetch(
        convId: String,
        loader: suspend () -> List<ChatMessage>?,
    ): List<ChatMessage>? {
        val entry = entries.getOrPut(convId) { Entry() }
        return entry.mutex.withLock {
            val age = nowMs() - entry.fetchedAtMs
            if (entry.hasSnapshot && age in 0 until freshForMs) {
                return@withLock entry.snapshot
            }

            val loaded = loader()
            entry.snapshot = loaded
            entry.fetchedAtMs = nowMs()
            entry.hasSnapshot = true
            loaded
        }
    }

    fun invalidate(convId: String) {
        entries[convId]?.hasSnapshot = false
    }
}
