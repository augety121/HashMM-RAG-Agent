package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DirectContextWindowTest {
    @Test
    fun `long direct chat preserves original goal and recent turns`() {
        val history = mutableListOf("user" to "原始目标：持续完善 HashMM")
        repeat(30) { index ->
            history += if (index % 2 == 0) "assistant" to "已报告步骤 $index"
            else "user" to "阶段要求 $index"
        }

        val compacted = DirectContextWindow.compact(history)

        assertEquals("system", compacted.first().first)
        assertTrue(compacted.first().second.contains("持续完善 HashMM"))
        assertEquals(history.takeLast(10), compacted.takeLast(10))
        assertTrue(compacted.size < history.size)
    }

    @Test
    fun `short direct chat remains byte for byte unchanged`() {
        val history = listOf("user" to "你好", "assistant" to "你好")
        assertEquals(history, DirectContextWindow.compact(history))
    }
}
