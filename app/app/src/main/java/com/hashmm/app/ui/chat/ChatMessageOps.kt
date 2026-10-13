package com.hashmm.app.ui.chat

import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.data.sync.ChatConversation

/**
 * V270 聊天消息列表整理——App 专属的消息重构逻辑（不是后端/桌面端的东西）。
 *
 * App 的消息流是"乐观本地占位 → 云端/后端规范列表覆盖"的两段式：发送时先塞一条本地气泡，随后
 * 拉到服务端规范列表再整体替换。这中间很容易出现"同一条消息相邻重复"（本地占位 + 规范返回同内容）。
 * 这里把整理逻辑抽成**纯函数**，既给 ChatDetailViewModel 复用，又能被 `./gradlew test` 直接单测——
 * 之前它是 ViewModel 的 private 方法，测不到。
 */
object ChatMessageOps {

    /**
     * 合并相邻的「同角色 + 同内容」消息（内容非空才去重）——消除乐观占位与规范返回撞车产生的重影。
     * 保持原始顺序；空内容（如流式刚建的占位）一律保留，不误删。
     */
    fun dedupeAdjacent(list: List<ChatMessage>): List<ChatMessage> {
        if (list.size < 2) return list
        val out = ArrayList<ChatMessage>(list.size)
        for (m in list) {
            val last = out.lastOrNull()
            if (last != null && last.role == m.role && last.content == m.content && m.content.isNotBlank()) continue
            out.add(m)
        }
        return out
    }

    /**
     * V272→V273 会话按最近活动倒序（最新在顶）。**预计算排序键一次/项**（decorate-sort-undecorate），
     * 避免 sortedByDescending 在每次比较时重复解析 ISO——20000 会话从 ~30 万次解析降到 2 万次，
     * 排序从秒级降到百毫秒级。既修压力测试，也让真实用户的会话列表排序更快更跟手。
     */
    fun <T> sortByRecencyDesc(items: List<T>, updatedAt: (T) -> String, createdAt: (T) -> String = { "" }): List<T> =
        items.map { it to convSortKey(updatedAt(it), createdAt(it)) }
            .sortedByDescending { it.second }
            .map { it.first }

    /**
     * History drawer order is based on actual chat activity, not metadata
     * edits such as rename or pin. `last_message_at` is maintained by the
     * Supabase message trigger; old schemas safely fall back to updated/created.
     * Pinned conversations form a stable group and each group is newest first.
     */
    fun sortConversationsByActivity(items: List<ChatConversation>): List<ChatConversation> =
        items.withIndex()
            .map { indexed ->
                val c = indexed.value
                val messageKey = convSortKey(c.lastMessageAt)
                val fallbackKey = convSortKey(c.updatedAt, c.createdAt)
                ConversationOrder(indexed.index, c, if (messageKey > 0L) messageKey else fallbackKey)
            }
            .sortedWith(
                compareByDescending<ConversationOrder> { it.conversation.pinned }
                    .thenByDescending { it.activityKey }
                    .thenBy { it.sourceIndex }
            )
            .map { it.conversation }

    private data class ConversationOrder(
        val sourceIndex: Int,
        val conversation: ChatConversation,
        val activityKey: Long,
    )

    /**
     * Timestamp shown beside a history row. It must use the same activity
     * source as history sorting; otherwise a rename can show a fresh time for
     * an old conversation while the row is correctly sorted by last message.
     */
    fun conversationActivityTimestamp(conversation: ChatConversation): String =
        conversation.lastMessageAt.ifBlank {
            conversation.updatedAt.ifBlank { conversation.createdAt }
        }

    /**
     * Chat transcript order shared by Supabase, the desktop client and App:
     * oldest first, newest last. Unknown timestamps stay after known rows and
     * retain their source order, so a live placeholder cannot jump into history.
     */
    fun sortChronological(items: List<ChatMessage>): List<ChatMessage> =
        items.withIndex()
            .map { indexed ->
                val key = convSortKey(indexed.value.createdAt, indexed.value.updatedAt)
                Triple(indexed.index, indexed.value, if (key == 0L) Long.MAX_VALUE else key)
            }
            .sortedWith(compareBy<Triple<Int, ChatMessage, Long>> { it.third }.thenBy { it.first })
            .map { it.second }

    /**
     * 是否存在仍在生成中的消息（status=="streaming"）——App 的"重连轮询是否该继续"判据。
     * 抽出来单测，保证判据稳定（这条链断了 App 会"一直转/一直空白"）。
     */
    fun hasStreaming(list: List<ChatMessage>): Boolean = list.any { it.status == "streaming" }

    /**
     * V272 会话列表排序键：把 updatedAt（ISO 或 epoch 字符串）解析成可比毫秒，供"最新在顶"倒序。
     * updatedAt 空则回退 createdAt；都空/无法解析返回 0（排最后）。ISO-8601 UTC 串本身按字典序即
     * 时序，但混入 epoch/空值时按字典序会错位——统一解析成 Long 才稳。
     */
    fun convSortKey(updatedAt: String, createdAt: String = ""): Long {
        fun toMillis(raw: String): Long? {
            val s = raw.trim()
            if (s.isBlank()) return null
            // 1) 整数 epoch（秒或毫秒）
            s.toLongOrNull()?.let { return if (it < 1_000_000_000_000L) it * 1000 else it }
            // 2) V306 修真 bug：**浮点 epoch**（如 "1783821959.0"）——后端 SQLite 的 REAL 时间戳
            //    同步到云端后就是这个样子。旧实现 toLongOrNull() 对带小数点的串返回 null，接着
            //    ISO 解析也失败 → 排序键全部退化成 0 → sortedByDescending 是稳定排序 → 原样保留
            //    输入顺序（云端增量同步是 ASCENDING/最旧在前）→ **会话列表最新的沉到最底下**。
            s.toDoubleOrNull()?.let {
                val ms = if (it < 1_000_000_000_000.0) it * 1000.0 else it
                return ms.toLong()
            }
            // 3) 标准 ISO-8601（带时区："...Z" / "...+08:00"）
            try { return java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli() } catch (_: Exception) {}
            try { return java.time.Instant.parse(s).toEpochMilli() } catch (_: Exception) {}
            // 4) Postgres/Supabase 常见文本："2026-07-11 10:09:28+00" / "2026-07-11 10:09:28.123456+00"
            //    —— 空格分隔 + 两位短偏移，OffsetDateTime/LocalDateTime 都直接解析不了，先归一化。
            var t = s.replace(' ', 'T')
            if (t.contains('T')) {   // 守卫：纯日期 "2026-07-11" 的 "-11" 不能被当成时区偏移
                Regex("([+-])(\\d{2})$").find(t)?.let { m ->
                    t = t.dropLast(3) + m.groupValues[1] + m.groupValues[2] + ":00"
                }
            }
            try { return java.time.OffsetDateTime.parse(t).toInstant().toEpochMilli() } catch (_: Exception) {}
            // 5) 无时区的本地时间："2026-07-11T10:09:28[.123]"
            try {
                return java.time.LocalDateTime.parse(t).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
            } catch (_: Exception) {}
            // 6) 纯日期 "2026-07-11" → 当天 00:00 UTC
            return try {
                java.time.LocalDate.parse(t).atStartOfDay().toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
            } catch (_: Exception) { null }
        }
        return toMillis(updatedAt) ?: toMillis(createdAt) ?: 0L
    }

    /**
     * 用服务端规范列表覆盖本地，但**保留仍在生成的本地尾巴**：当服务端还没落库那条 streaming 占位
     * 时，直接替换会让正在生成的气泡闪没。规则：若本地最后一条是 streaming 且服务端列表里没有同 id，
     * 则把它接在服务端列表尾部。
     */
    fun reconcile(local: List<ChatMessage>, server: List<ChatMessage>): List<ChatMessage> {
        if (server.isEmpty()) return local
        val tail = local.lastOrNull()
        if (tail != null && tail.status == "streaming" && server.none { it.id == tail.id }) {
            return dedupeAdjacent(server + tail)
        }
        return dedupeAdjacent(server)
    }
}
