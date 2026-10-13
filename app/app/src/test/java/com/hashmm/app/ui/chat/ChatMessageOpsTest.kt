package com.hashmm.app.ui.chat

import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.data.sync.ChatConversation
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * V270 App 专属·聊天消息列表整理单元测试（测的是 App 自己的消息重构逻辑，与后端/桌面端无关）。
 * 覆盖 App 特有的"乐观占位 → 规范覆盖"两段式消息流会遇到的重影/闪没问题。纯 JVM，`./gradlew test`。
 */
class ChatMessageOpsTest {

    private fun msg(id: String, role: String, content: String, status: String = "complete") =
        ChatMessage(id = id, convId = "c", role = role, content = content, thinking = "", status = status, createdAt = "")

    @Test fun dedupe_removesAdjacentSameRoleSameContent() {
        val input = listOf(
            msg("1", "user", "你好"),
            msg("2", "assistant", "在的"),
            msg("3", "assistant", "在的"),   // 与上一条同角色同内容 → 去重
        )
        val out = ChatMessageOps.dedupeAdjacent(input)
        assertEquals(2, out.size)
        assertEquals("在的", out.last().content)
    }

    @Test fun dedupe_keepsSameContentDifferentRole() {
        val input = listOf(msg("1", "user", "重复"), msg("2", "assistant", "重复"))
        // 角色不同即使内容相同也不去重（用户问“重复”，助手也回“重复”是合法的）
        assertEquals(2, ChatMessageOps.dedupeAdjacent(input).size)
    }

    @Test fun dedupe_keepsBlankPlaceholders() {
        val input = listOf(
            msg("1", "user", "问题"),
            msg("2", "assistant", "", status = "streaming"),
            msg("3", "assistant", "", status = "streaming"),   // 空内容不去重（占位气泡都保留）
        )
        assertEquals(3, ChatMessageOps.dedupeAdjacent(input).size)
    }

    @Test fun dedupe_preservesOrderAndNonAdjacentDup() {
        val input = listOf(msg("1", "a", "X"), msg("2", "a", "Y"), msg("3", "a", "X"))
        // 非相邻的同内容不去重，且顺序保持
        val out = ChatMessageOps.dedupeAdjacent(input)
        assertEquals(listOf("X", "Y", "X"), out.map { it.content })
    }

    @Test fun dedupe_shortListUntouched() {
        assertEquals(0, ChatMessageOps.dedupeAdjacent(emptyList()).size)
        val one = listOf(msg("1", "user", "只有一条"))
        assertEquals(1, ChatMessageOps.dedupeAdjacent(one).size)
    }

    @Test fun hasStreaming_detectsGenerating() {
        assertTrue(ChatMessageOps.hasStreaming(listOf(msg("1", "assistant", "半截", status = "streaming"))))
        assertFalse(ChatMessageOps.hasStreaming(listOf(msg("1", "assistant", "完成", status = "complete"))))
        assertFalse(ChatMessageOps.hasStreaming(listOf(msg("1", "assistant", "需要补充", status = "waiting_input"))))
    }

    @Test fun reconcile_keepsStreamingTailNotYetOnServer() {
        // 服务端还没落库那条正在生成的占位 → 覆盖时必须保留本地尾巴，否则气泡闪没
        val server = listOf(msg("u1", "user", "问题"))
        val local = listOf(msg("u1", "user", "问题"), msg("a-local", "assistant", "生成中…", status = "streaming"))
        val out = ChatMessageOps.reconcile(local, server)
        assertEquals(2, out.size)
        assertEquals("streaming", out.last().status)
        assertEquals("生成中…", out.last().content)
    }

    @Test fun reconcile_replacesWhenServerHasEverything() {
        val server = listOf(msg("u1", "user", "问题"), msg("a1", "assistant", "完整答案", status = "complete"))
        val local = listOf(msg("u1", "user", "问题"), msg("a1", "assistant", "旧的半截", status = "streaming"))
        // 服务端已有对应完成消息 → 用服务端规范列表（本地 streaming 尾巴 id 在服务端存在，不再附加）
        val out = ChatMessageOps.reconcile(local, server)
        assertEquals(2, out.size)
        assertEquals("完整答案", out.last().content)
        assertEquals("complete", out.last().status)
    }

    @Test fun reconcile_emptyServerKeepsLocal() {
        val local = listOf(msg("1", "user", "离线也要能看历史"))
        assertEquals(local, ChatMessageOps.reconcile(local, emptyList()))
    }

    @Test fun convSortKey_isoOrdersNewestFirst() {
        val older = ChatMessageOps.convSortKey("2026-07-01T08:00:00Z")
        val newer = ChatMessageOps.convSortKey("2026-07-10T08:00:00Z")
        assertTrue("较新的 ISO 时间应有更大的排序键", newer > older)
    }

    @Test fun convSortKey_fallbackToCreatedAtWhenUpdatedBlank() {
        val k = ChatMessageOps.convSortKey("", "2026-07-05T00:00:00Z")
        assertTrue("updatedAt 空时应回退 createdAt", k > 0)
    }

    @Test fun convSortKey_blankIsZeroSortsLast() {
        assertEquals(0L, ChatMessageOps.convSortKey("", ""))
    }

    @Test fun convSortKey_epochSecondsSupported() {
        // 纯数字 epoch 秒也要能比（1751328000 = 2025-07-01）
        val k = ChatMessageOps.convSortKey("1751328000")
        assertTrue("epoch 秒应被解析成毫秒级排序键", k > 1_000_000_000_000L)
    }

    @Test fun convSortKey_sortsListNewestFirst() {
        data class C(val id: String, val u: String)
        val convs = listOf(C("a", "2026-07-01T00:00:00Z"), C("b", "2026-07-10T00:00:00Z"), C("c", "2026-07-05T00:00:00Z"))
        val sorted = convs.sortedByDescending { ChatMessageOps.convSortKey(it.u) }
        assertEquals(listOf("b", "c", "a"), sorted.map { it.id })   // 最新 b 在最前
    }

    // ══ V306 回归：会话列表"最新沉到最底下"的真 bug ══
    // 根因：后端 SQLite 的 updated_at 是 REAL(浮点 epoch)，同步到云端就是 "1783821959.0"。
    // 旧 convSortKey 的 toLongOrNull() 对带小数点的串返回 null，ISO 解析也失败 → 排序键全为 0
    // → sortedByDescending 稳定排序原样保留输入序（云端增量同步是 ASCENDING/最旧在前）→ 反了。

    @Test fun convSortKey_epochFloat_parsed() {
        // 关键回归：浮点 epoch 秒必须解析出正数键（旧实现返回 0）
        assertEquals(1783821959000L, ChatMessageOps.convSortKey("1783821959.0"))
    }

    @Test fun convSortKey_epochFloatMillis_parsed() {
        assertEquals(1783821959000L, ChatMessageOps.convSortKey("1783821959000.0"))
    }

    @Test fun convSortKey_postgresSpaceShortOffset_parsed() {
        // Supabase/Postgres 文本时间："2026-07-11 10:09:28+00"（空格分隔 + 两位短偏移）
        assertTrue(ChatMessageOps.convSortKey("2026-07-11 10:09:28+00") > 0L)
    }

    @Test fun convSortKey_localDateTimeNoZone_parsed() {
        assertTrue(ChatMessageOps.convSortKey("2026-07-11T10:09:28") > 0L)
    }

    @Test fun convSortKey_dateOnly_parsed() {
        assertTrue(ChatMessageOps.convSortKey("2026-07-11") > 0L)
    }

    @Test fun convSortKey_illegalStillZero() {
        assertEquals(0L, ChatMessageOps.convSortKey("这不是时间"))
        assertEquals(0L, ChatMessageOps.convSortKey(""))
    }

    @Test fun sortByRecencyDesc_epochFloats_newestFirst() {
        // 端到端：云端按 ASCENDING(最旧在前)返回的浮点 epoch 列表 → 必须被翻成"最新在顶"
        data class C(val id: String, val u: String)
        val fromCloudAscending = listOf(
            C("old", "1783000000.0"), C("mid", "1783500000.0"), C("new", "1783821959.0"),
        )
        val sorted = ChatMessageOps.sortByRecencyDesc(fromCloudAscending, { it.u }, { "" })
        assertEquals(listOf("new", "mid", "old"), sorted.map { it.id })
    }

    @Test fun sortChronological_keepsNewestAtBottom() {
        val input = listOf(
            msg("new", "assistant", "new").copy(createdAt = "2026-07-10T00:00:00Z"),
            msg("old", "user", "old").copy(createdAt = "2026-07-01T00:00:00Z"),
            msg("mid", "assistant", "mid").copy(createdAt = "2026-07-05T00:00:00Z"),
        )
        assertEquals(listOf("old", "mid", "new"), ChatMessageOps.sortChronological(input).map { it.id })
    }

    @Test fun conversationHistory_usesLastMessageNotMetadataEdit() {
        val renamedOldChat = ChatConversation(
            id = "old", updatedAt = "2026-07-20T00:00:00Z",
            lastMessageAt = "2026-07-01T00:00:00Z",
        )
        val recentlyActiveChat = ChatConversation(
            id = "active", updatedAt = "2026-07-10T00:00:00Z",
            lastMessageAt = "2026-07-19T00:00:00Z",
        )
        assertEquals(
            listOf("active", "old"),
            ChatMessageOps.sortConversationsByActivity(listOf(renamedOldChat, recentlyActiveChat)).map { it.id },
        )
    }

    @Test fun conversationHistory_fallsBackForOldSchemaAndKeepsPinnedGroup() {
        val normal = ChatConversation(id = "normal", updatedAt = "2026-07-20T00:00:00Z")
        val pinned = ChatConversation(id = "pinned", pinned = true, updatedAt = "2026-07-01T00:00:00Z")
        assertEquals(
            listOf("pinned", "normal"),
            ChatMessageOps.sortConversationsByActivity(listOf(normal, pinned)).map { it.id },
        )
    }

    @Test fun conversationHistory_displaysLastMessageTimeInsteadOfRenameTime() {
        val renamed = ChatConversation(
            id = "renamed",
            createdAt = "2026-06-01T00:00:00Z",
            updatedAt = "2026-07-20T00:00:00Z",
            lastMessageAt = "2026-07-02T08:30:00Z",
        )
        assertEquals(
            "2026-07-02T08:30:00Z",
            ChatMessageOps.conversationActivityTimestamp(renamed),
        )
    }
}
