package com.hashmm.app.data.cache

import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.data.sync.ChatMessage
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 本地数据读取层（离线优先）。会话/消息以加密 JSON 落在本账号目录，读取快、离线可用。
 *
 * V306 消息分页存储（修 APP-P1-01：整会话 JSON 全量读写）：
 *  · 旧实现 putMessages 每次把整会话消息数组重新序列化 + AES-GCM 加密 + 整文件重写，
 *    长会话下每追加一条都是 O(n) 写放大（序列化/加密/磁盘），列表更新抖动。
 *  · 新实现把每个会话的消息切成固定大小的**页**（messages_{conv}__p{k}，每页 PAGE_SIZE 条），
 *    并维护一个轻量索引（messages_{conv}__idx，记录每页的 [条数, 内容指纹]）。
 *    putMessages 只重写**指纹变化的页**——纯追加只动最后一页；编辑只动所在页；
 *    收缩则删除多余页。写成本从 O(n) 降到 ≈O(页大小)。
 *  · 向后兼容：旧的单文件 messages_{conv} 仍可被 getMessages 读出（读时回退），
 *    并在下次 putMessages 迁移成分页后删除旧单文件。
 *
 * 公开 API 与旧版**完全一致**（getMessages/putMessages/getConversations/... 签名不变），
 * 因此 SyncRepository 等调用方无需改动。
 */
@Singleton
class LocalStore @Inject constructor(
    private val cache: SecureCache,
) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val epoch = "1970-01-01T00:00:00Z"

    @Serializable
    private data class PageFp(val n: Int, val h: Int)

    @Serializable
    private data class MsgIndex(val pageSize: Int, val count: Int, val pages: List<PageFp>)

    // ── 会话（量小、低频，仍整存）──
    fun getConversations(userId: String): List<ChatConversation> =
        cache.read(userId, "conversations")?.let {
            runCatching { json.decodeFromString<List<ChatConversation>>(it) }.getOrNull()
        } ?: emptyList()

    fun putConversations(userId: String, list: List<ChatConversation>) =
        cache.write(userId, "conversations", json.encodeToString(list))

    // ── 消息（按会话，分页存储）──
    private fun idxKey(convId: String) = "messages_${convId}__idx"
    private fun pageKey(convId: String, k: Int) = "messages_${convId}__p$k"
    private fun legacyKey(convId: String) = "messages_$convId"

    /** 读取某会话全部消息：优先分页；无索引则回退旧单文件格式。永不抛错。 */
    fun getMessages(userId: String, convId: String): List<ChatMessage> {
        val idxRaw = cache.read(userId, idxKey(convId))
        if (idxRaw == null) {
            // 旧格式（单文件）回退——升级后既有数据不丢
            val blob = cache.read(userId, legacyKey(convId)) ?: return emptyList()
            return runCatching { json.decodeFromString<List<ChatMessage>>(blob) }.getOrDefault(emptyList())
        }
        val idx = runCatching { json.decodeFromString<MsgIndex>(idxRaw) }.getOrNull() ?: return emptyList()
        val out = ArrayList<ChatMessage>(idx.count)
        for (k in idx.pages.indices) {
            val pg = cache.read(userId, pageKey(convId, k)) ?: continue
            runCatching { json.decodeFromString<List<ChatMessage>>(pg) }.getOrNull()?.let { out.addAll(it) }
        }
        return out
    }

    /** 覆盖某会话的消息列表：只重写指纹变化的页，删除多余页，迁移旧单文件。 */
    fun putMessages(userId: String, convId: String, list: List<ChatMessage>) {
        val pageSize = PAGE_SIZE
        val pages = if (list.isEmpty()) emptyList() else list.chunked(pageSize)

        val oldIdx = cache.read(userId, idxKey(convId))?.let {
            runCatching { json.decodeFromString<MsgIndex>(it) }.getOrNull()
        }
        val oldPages: List<PageFp> = oldIdx?.pages ?: emptyList()

        val newFps = ArrayList<PageFp>(pages.size)
        for (k in pages.indices) {
            val pageJson = json.encodeToString(pages[k])
            val fp = PageFp(pages[k].size, pageJson.hashCode())
            newFps.add(fp)
            // 仅当该页是新页或指纹变化时才写（纯追加只动最后一页）
            if (k >= oldPages.size || oldPages[k] != fp) {
                cache.write(userId, pageKey(convId, k), pageJson)
            }
        }
        // 列表收缩：删除多余的旧页
        for (k in pages.size until oldPages.size) {
            cache.delete(userId, pageKey(convId, k))
        }
        cache.write(userId, idxKey(convId),
            json.encodeToString(MsgIndex(pageSize, list.size, newFps)))
        // 迁移完成后删除旧单文件（若存在），避免双份存储
        cache.delete(userId, legacyKey(convId))
    }

    /** Apply a server-owned deletion marker without leaking across owners. */
    fun removeConversation(userId: String, convId: String) {
        val idx = cache.read(userId, idxKey(convId))?.let {
            runCatching { json.decodeFromString<MsgIndex>(it) }.getOrNull()
        }
        for (page in 0 until (idx?.pages?.size ?: 0)) {
            cache.delete(userId, pageKey(convId, page))
        }
        cache.delete(userId, idxKey(convId))
        cache.delete(userId, legacyKey(convId))
        putConversations(userId, getConversations(userId).filterNot { it.id == convId })
    }

    // ── 上次同步时间（按类别，如 "conversations"）──
    fun getLastSync(userId: String, key: String): String =
        cache.read(userId, "lastsync_$key") ?: epoch

    fun setLastSync(userId: String, key: String, ts: String) =
        cache.write(userId, "lastsync_$key", ts)

    /** Dynamic/workbench snapshot. It uses the same per-account encrypted
     * cache as conversations, so App screens can share one server read and
     * still render the last verified state after a process restart. */
    fun getFeedSnapshot(userId: String): String? = cache.read(userId, "feed_snapshot")

    fun putFeedSnapshot(userId: String, value: String) = cache.write(userId, "feed_snapshot", value)

    /** Server-projected Agent capability state shared by Chat, desktop and App. */
    fun getCapabilitySnapshot(userId: String): String? = cache.read(userId, "runtime_capabilities")

    fun putCapabilitySnapshot(userId: String, value: String) =
        cache.write(userId, "runtime_capabilities", value)

    /** V373 owner-bound work feed (cursor + latest run projections). */
    fun getWorkRuntimeSnapshot(userId: String): String? = cache.read(userId, "work_runtime")

    fun putWorkRuntimeSnapshot(userId: String, value: String) =
        cache.write(userId, "work_runtime", value)

    /** Stable-id work commands/decisions awaiting server reconciliation.
     * This is owner-scoped and encrypted by SecureCache just like the work
     * snapshot. It never stores access tokens or executable tool arguments. */
    fun getWorkRuntimeOutbox(userId: String): String? =
        cache.read(userId, "work_runtime_outbox")

    fun putWorkRuntimeOutbox(userId: String, value: String) =
        cache.write(userId, "work_runtime_outbox", value)

    fun clear(userId: String) = cache.clear(userId)

    companion object {
        /** 每页消息条数。200 条/页在写放大与读放大之间取平衡（追加只重写≤200 条的一页）。 */
        const val PAGE_SIZE = 200
    }
}
