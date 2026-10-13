package com.hashmm.app.ui.chat

import com.hashmm.app.data.sync.ChatMessage
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
}
