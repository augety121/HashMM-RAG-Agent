package com.hashmm.app.data.sync

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.cache.LocalStore
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.postgrest.postgrest
import io.github.jan.supabase.postgrest.query.Order
import io.github.jan.supabase.postgrest.query.filter.FilterOperator
import io.github.jan.supabase.realtime.PostgresAction
import io.github.jan.supabase.realtime.channel
import io.github.jan.supabase.realtime.postgresChangeFlow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

enum class ConversationSyncState {
    FRESH,
    VERIFIED_EMPTY,
    OFFLINE_CACHED,
    ERROR,
    SIGNED_OUT,
}

data class ConversationSyncSnapshot(
    val conversations: List<ChatConversation>,
    val state: ConversationSyncState,
    val failureClass: String? = null,
    val synchronizedAt: Long? = null,
)

/**
 * 云端同步读取 + 离线优先本地缓存（对标大厂的增量同步）。
 * RLS 保证每个查询只返回当前登录用户自己的数据。
 *
 * 增量同步策略：
 *  · 读：先回本地缓存（秒显、离线可用），再增量拉变更覆盖。
 *  · 增量：只取 updated_at > 上次同步 的记录，按主键 upsert 进本地（变更覆盖、未变保留），
 *         并把本次最大 updated_at 记为新的「上次同步」。
 *  · RAG 大文件 / 向量不在此层 —— 由 AutoDL 私有云直连。
 */
@Singleton
class SyncRepository @Inject constructor(
    private val supabase: SupabaseClient,
    private val auth: AuthRepository,
    private val local: LocalStore,
    private val backend: BackendChatSyncGateway,
) {
    // ── 本地缓存读取（离线优先）──
    suspend fun cachedConversations(): List<ChatConversation> = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext emptyList()
        com.hashmm.app.ui.chat.ChatMessageOps.sortConversationsByActivity(local.getConversations(uid))
    }

    suspend fun cachedMessages(convId: String): List<ChatMessage> = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext emptyList()
        com.hashmm.app.ui.chat.ChatMessageOps.sortChronological(local.getMessages(uid, convId))
    }

    /** 把一份消息写入本地缓存（如客户端直连拿到的实时消息，供下次离线秒显）。 */
    suspend fun cacheMessages(convId: String, list: List<ChatMessage>) = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext
        local.putMessages(uid, convId, list)
    }

    // ── 增量同步会话列表 → 本地缓存；返回合并后的本地会话 ──
    suspend fun syncConversations(): List<ChatConversation> =
        syncConversationSnapshot().conversations

    suspend fun syncConversationSnapshot(): ConversationSyncSnapshot = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext ConversationSyncSnapshot(
            conversations = emptyList(), state = ConversationSyncState.SIGNED_OUT,
            failureClass = "authentication_required",
        )
        val tombstoneSince = local.getLastSync(uid, "conversation_tombstones").toDoubleOrNull() ?: 0.0
        val canonical = runCatching { backend.conversations(tombstoneSince) }.getOrNull()
        if (canonical != null && canonical.complete) {
            val merged = local.getConversations(uid).associateBy { it.id }.toMutableMap()
            canonical.conversations.forEach { merged[it.id] = it.copy(userId = uid) }
            canonical.tombstoneIds.forEach { id ->
                merged.remove(id)
                local.removeConversation(uid, id)
            }
            val list = com.hashmm.app.ui.chat.ChatMessageOps.sortConversationsByActivity(merged.values.toList())
            local.putConversations(uid, list)
            local.setLastSync(uid, "conversation_tombstones", canonical.tombstoneCursor.toString())
            return@withContext ConversationSyncSnapshot(
                conversations = list,
                state = if (list.isEmpty()) ConversationSyncState.VERIFIED_EMPTY else ConversationSyncState.FRESH,
                synchronizedAt = System.currentTimeMillis(),
            )
        }
        // Backend is the sole conversation authority. Supabase Realtime only
        // wakes this repository; it is not a fallback read model.
        val cached = com.hashmm.app.ui.chat.ChatMessageOps
            .sortConversationsByActivity(local.getConversations(uid))
        return@withContext ConversationSyncSnapshot(
            conversations = cached,
            state = if (cached.isNotEmpty()) ConversationSyncState.OFFLINE_CACHED else ConversationSyncState.ERROR,
            failureClass = backend.lastConversationFailure ?: "network_error",
        )
    }

    /** 拉取当前用户的记忆（user_memory，量小直接全量；RLS 仅返回本人）。 */
    suspend fun syncMemories(): List<UserMemory> = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext emptyList()
        try {
            supabase.postgrest.from("user_memory").select {
                filter { eq("user_id", uid) }
                order("category", Order.ASCENDING)
                limit(500L)
            }.decodeList<UserMemory>()
        } catch (e: Exception) {
            emptyList()
        }
    }

    // ── 增量同步某会话消息 → 本地缓存；返回合并后的本地消息 ──
    suspend fun syncMessages(convId: String): List<ChatMessage> = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext emptyList()
        val pendingKey = "pending_direct_$convId"
        val pendingIds = local.getLastSync(uid, pendingKey)
            .split(',').map { it.trim() }.filter { it.isNotBlank() }.toSet()
        if (pendingIds.isNotEmpty()) {
            val pending = local.getMessages(uid, convId).filter { it.id in pendingIds }
            if (pending.isEmpty() || runCatching { backend.appendMessages(convId, pending) }.getOrDefault(false)) {
                local.setLastSync(uid, pendingKey, "")
            }
        }
        val canonical = runCatching { backend.messages(convId) }.getOrNull()
        if (canonical != null) {
            val merged = local.getMessages(uid, convId).associateBy { it.id }.toMutableMap()
            canonical.forEach { merged[it.id] = it.copy(userId = uid) }
            val list = com.hashmm.app.ui.chat.ChatMessageOps.sortChronological(merged.values.toList())
            local.putMessages(uid, convId, list)
            return@withContext list
        }
        // Preserve the offline snapshot when the backend is unavailable.
        // Never switch to direct Supabase rows with a different schema/truth.
        return@withContext com.hashmm.app.ui.chat.ChatMessageOps
            .sortChronological(local.getMessages(uid, convId))
    }

    /** V250（直连消息互通）：把消息 upsert 进 Supabase chat_messages——手机直连轮次也入云，
     *  桌面端（配置 service_role 同步后）可拉到同一会话继续。失败静默（离线不拦对话）。 */
    suspend fun upsertMessages(list: List<ChatMessage>): Boolean = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext false
        if (list.isEmpty()) return@withContext false
        val canonicalRows = list.map { it.copy(userId = uid) }
        val canonicalConvId = canonicalRows.first().convId
        if (canonicalConvId.isNotBlank()) {
            val merged = local.getMessages(uid, canonicalConvId).associateBy { it.id }.toMutableMap()
            canonicalRows.forEach { merged[it.id] = it }
            local.putMessages(
                uid, canonicalConvId,
                com.hashmm.app.ui.chat.ChatMessageOps.sortChronological(merged.values.toList()),
            )
        }
        val delivered = canonicalConvId.isNotBlank() &&
            runCatching { backend.appendMessages(canonicalConvId, canonicalRows) }.getOrDefault(false)
        if (canonicalConvId.isNotBlank()) {
            local.setLastSync(
                uid,
                "pending_direct_$canonicalConvId",
                if (delivered) "" else canonicalRows.joinToString(",") { it.id },
            )
        }
        return@withContext delivered
    }

    // ── Realtime 实时联动：对端一改，立刻回调（上层据此重新增量同步）──
    suspend fun subscribeConversations(onChange: () -> Unit) =
        subscribeTable("chat_conversations", null, onChange)

    suspend fun subscribeMessages(convId: String, onChange: () -> Unit) =
        subscribeTable("chat_messages", convId, onChange)

    private suspend fun subscribeTable(tableName: String, convId: String?, onChange: () -> Unit) {
        val uid = auth.currentUserId() ?: return
        val uniqueId = UUID.randomUUID().toString().take(8)
        val channel = supabase.channel("hashmm-$tableName-$uid-$uniqueId")
        try {
            val changes = channel.postgresChangeFlow<PostgresAction>(schema = "public") {
                table = tableName
                if (convId != null) {
                    filter(column = "conv_id", operator = FilterOperator.EQ, value = convId)
                } else {
                    filter(column = "user_id", operator = FilterOperator.EQ, value = uid)
                }
            }
            channel.subscribe(blockUntilSubscribed = false)
            changes.collect { onChange() }
        } finally {
            CoroutineScope(Dispatchers.IO).launch { runCatching { channel.unsubscribe() } }
        }
    }
}
