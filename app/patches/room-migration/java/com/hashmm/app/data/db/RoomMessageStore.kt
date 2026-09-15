package com.hashmm.app.data.db

import com.hashmm.app.data.sync.ChatMessage
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/**
 * RoomMessageStore — 把 LocalStore 的消息读写换成 Room（V306）。
 *
 * 用法：把 LocalStore 里 getMessages/putMessages 的实现替换为委托给本类（见 README 第 3 步）。
 * 公开语义与旧分页方案一致，但底层是真数据库：
 *  · putMessages（覆盖整会话）→ 一个事务 deleteConv + upsertAll；
 *  · appendMessage（单条追加）→ 单行 upsert（O(1)，不再整会话重写）；
 *  · getMessages → 走索引的排序查询；也可用 messagesPage 分页取。
 */
class RoomMessageStore(private val dao: MessageDao) {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private fun ChatMessage.toEntity(): MessageEntity = MessageEntity(
        id = id, convId = convId, userId = userId, role = role, content = content,
        thinking = thinking,
        toolCallsJson = toolCalls?.let { json.encodeToString(JsonElement.serializer(), it) },
        filesJson = files?.let { json.encodeToString(JsonElement.serializer(), it) },
        sourcesJson = sources?.let { json.encodeToString(JsonElement.serializer(), it) },
        suggestionsJson = suggestions?.let { json.encodeToString(JsonElement.serializer(), it) },
        status = status, tokensIn = tokensIn, tokensOut = tokensOut,
        createdAt = createdAt, updatedAt = updatedAt,
        sortKey = parseMillis(if (updatedAt.isNotBlank()) updatedAt else createdAt),
    )

    private fun MessageEntity.toModel(): ChatMessage = ChatMessage(
        id = id, convId = convId, userId = userId, role = role, content = content,
        thinking = thinking,
        toolCalls = toolCallsJson?.let { json.parseToJsonElement(it) },
        files = filesJson?.let { json.parseToJsonElement(it) },
        sources = sourcesJson?.let { json.parseToJsonElement(it) },
        suggestions = suggestionsJson?.let { json.parseToJsonElement(it) },
        status = status, tokensIn = tokensIn, tokensOut = tokensOut,
        createdAt = createdAt, updatedAt = updatedAt,
    )

    suspend fun getMessages(convId: String): List<ChatMessage> =
        dao.messagesOf(convId).map { it.toModel() }

    suspend fun getMessagesPage(convId: String, limit: Int, offset: Int): List<ChatMessage> =
        dao.messagesPage(convId, limit, offset).map { it.toModel() }

    /** 覆盖整会话（服务端同步下来的最新列表）。 */
    suspend fun putMessages(convId: String, list: List<ChatMessage>) =
        dao.replaceConv(convId, list.map { it.toEntity() })

    /** 单条追加/更新（本地流式产生新消息时用，O(1) 单行写）。 */
    suspend fun appendMessage(msg: ChatMessage) = dao.upsert(msg.toEntity())

    suspend fun clear() = dao.clearAll()

    companion object {
        /** 与 ChatMessageOps.convSortKey 同口径：ISO/epoch 秒/毫秒 → 毫秒；非法→0。 */
        fun parseMillis(s: String): Long {
            if (s.isBlank()) return 0L
            s.toLongOrNull()?.let { return if (it > 1_000_000_000_000L) it else it * 1000L }
            return try {
                java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli()
            } catch (e: Exception) {
                try {
                    java.time.LocalDateTime.parse(s)
                        .toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
                } catch (e2: Exception) {
                    0L
                }
            }
        }
    }
}
