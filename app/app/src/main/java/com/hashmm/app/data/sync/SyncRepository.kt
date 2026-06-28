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
) {
    // ── 本地缓存读取（离线优先）──
    suspend fun cachedConversations(): List<ChatConversation> = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext emptyList()
        local.getConversations(uid)
    }

    suspend fun cachedMessages(convId: String): List<ChatMessage> = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext emptyList()
        local.getMessages(uid, convId)
    }

    /** 把一份消息写入本地缓存（如客户端直连拿到的实时消息，供下次离线秒显）。 */
    suspend fun cacheMessages(convId: String, list: List<ChatMessage>) = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext
        local.putMessages(uid, convId, list)
    }

    // ── 增量同步会话列表 → 本地缓存；返回合并后的本地会话 ──
    suspend fun syncConversations(): List<ChatConversation> = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId() ?: return@withContext emptyList()
        val since = local.getLastSync(uid, "conversations")
        try {
            val changed = supabase.postgrest.from("chat_conversations").select {
                filter { gt("updated_at", since) }
                order("updated_at", Order.ASCENDING)
                limit(500L)
            }.decodeList<ChatConversation>()
            if (changed.isNotEmpty()) {
                val merged = local.getConversations(uid).associateBy { it.id }.toMutableMap()
                changed.forEach { merged[it.id] = it }                 // 变更覆盖、未变保留
                val list = merged.values.sortedByDescending { it.updatedAt }
                local.putConversations(uid, list)
                local.setLastSync(uid, "conversations", changed.maxOf { it.updatedAt })
            }
        } catch (e: Exception) {
            // 离线/失败：退回已有本地缓存
        }
        local.getConversations(uid)
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
        val key = "messages_$convId"
        val since = local.getLastSync(uid, key)
        try {
            val changed = supabase.postgrest.from("chat_messages").select {
                filter {
                    eq("conv_id", convId)
                    gt("updated_at", since)
                }
                order("updated_at", Order.ASCENDING)
                limit(1000L)
            }.decodeList<ChatMessage>()
            if (changed.isNotEmpty()) {
                val merged = local.getMessages(uid, convId).associateBy { it.id }.toMutableMap()
                changed.forEach { merged[it.id] = it }
                val list = merged.values.sortedBy { it.createdAt }
                local.putMessages(uid, convId, list)
                local.setLastSync(uid, key, changed.maxOf { it.updatedAt })
            }
        } catch (e: Exception) {
            // 离线/失败：退回本地缓存
        }
        local.getMessages(uid, convId)
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
