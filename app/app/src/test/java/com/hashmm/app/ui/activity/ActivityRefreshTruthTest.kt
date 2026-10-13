package com.hashmm.app.ui.activity

import com.hashmm.app.data.remote.UsageStat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ActivityRefreshTruthTest {
    @Test
    fun `a verified zero usage snapshot is still real data`() {
        val result = reconcileUsage(null, UsageStat(requests = 0, tokens = 0, cost = 0.0))

        assertEquals(0, result.snapshot?.requests)
        assertNull(result.error)
    }

    @Test
    fun `a failed refresh preserves the last verified usage snapshot`() {
        val previous = UsageStat(requests = 12, tokens = 340, cost = 1.5)
        val result = reconcileUsage(
            previous,
            UsageStat(requests = 0, tokens = 0, cost = 0.0, error = "network unavailable"),
        )

        assertEquals(previous, result.snapshot)
        assertEquals("network unavailable", result.error)
    }
}
