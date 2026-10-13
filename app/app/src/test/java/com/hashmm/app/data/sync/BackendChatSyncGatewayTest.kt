package com.hashmm.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BackendChatSyncGatewayTest {
    @Test
    fun conversationPageParsesCursorAndNumericTimestamps() {
        val page = parseBackendConversations(
            """{"conversations":[{"id":"c1","user_id":"u","title":"A","pinned":1,"created_at":1.0,"updated_at":2.0,"last_activity_at":3.0}],"page":{"has_more":true,"next_cursor":"next"}}""",
        )!!
        assertEquals("c1", page.first.single().id)
        assertEquals("3.0", page.first.single().lastMessageAt)
        assertTrue(page.first.single().pinned)
        assertTrue(page.second)
        assertEquals("next", page.third)
    }

    @Test
    fun tombstonesAndMessagesAreBoundedStructuredData() {
        val tombstones = parseBackendTombstones(
            """{"tombstones":[{"conversation_id":"c1"},{"conv_id":"c2"}],"next_since":9.5,"next_after_id":"c2","has_more":true}""",
        )!!
        assertEquals(setOf("c1", "c2"), tombstones.ids)
        assertEquals(9.5, tombstones.nextSince, 0.0)
        assertEquals("c2", tombstones.nextAfterId)
        assertTrue(tombstones.hasMore)
        val messages = parseBackendMessages(
            """{"messages":[{"id":"m1","conv_id":"c1","role":"assistant","content":"ok","sources":[{"id":1}],"created_at":4.0}],"page":{"has_more":false}}""",
        )!!
        assertEquals("m1", messages.first.single().id)
        assertEquals("4.0", messages.first.single().createdAt)
        assertFalse(messages.second)
        assertNull(messages.third)
    }

    @Test
    fun malformedPayloadFailsClosed() {
        assertNull(parseBackendConversations("not-json"))
        assertNull(parseBackendMessages("[]"))
    }
}
