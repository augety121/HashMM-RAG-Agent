package com.hashmm.app.data.cache

import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.data.sync.ChatMessage
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 本地数据读取层（离线优先）。会话/消息以加密 JSON 落在本账号目录，读取快、离线可用。
 * 增量同步只覆盖变更项、保留未变项；并记录每类的「上次同步时间」。
 */
@Singleton
class LocalStore @Inject constructor(
    private val cache: SecureCache,
) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val epoch = "1970-01-01T00:00:00Z"

    // ── 会话 ──
    fun getConversations(userId: String): List<ChatConversation> =
        cache.read(userId, "conversations")?.let {
            runCatching { json.decodeFromString<List<ChatConversation>>(it) }.getOrNull()
        } ?: emptyList()

    fun putConversations(userId: String, list: List<ChatConversation>) =
        cache.write(userId, "conversations", json.encodeToString(list))

    // ── 消息（按会话）──
    fun getMessages(userId: String, convId: String): List<ChatMessage> =
        cache.read(userId, "messages_$convId")?.let {
            runCatching { json.decodeFromString<List<ChatMessage>>(it) }.getOrNull()
        } ?: emptyList()

    fun putMessages(userId: String, convId: String, list: List<ChatMessage>) =
        cache.write(userId, "messages_$convId", json.encodeToString(list))

    // ── 上次同步时间（按类别，如 "conversations"）──
    fun getLastSync(userId: String, key: String): String =
        cache.read(userId, "lastsync_$key") ?: epoch

    fun setLastSync(userId: String, key: String, ts: String) =
        cache.write(userId, "lastsync_$key", ts)

    fun clear(userId: String) = cache.clear(userId)
}
