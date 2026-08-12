package com.hashmm.app.ui.workbench

import com.hashmm.app.data.remote.MobileWorkPresentation
import com.hashmm.app.data.remote.MobileWorkRun
import com.hashmm.app.ui.activity.TodayBucket
import com.hashmm.app.ui.activity.todayBucket
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.assertEquals
import org.junit.Test

class WorkSurfacePresentationTest {
    @Test
    fun todayPrioritizesExplicitUserAttentionOverRunState() {
        assertEquals(TodayBucket.ATTENTION, todayBucket("running", needsUser = true))
        assertEquals(TodayBucket.ATTENTION, todayBucket("waiting_approval", needsUser = false))
        assertEquals(TodayBucket.ACTIVE, todayBucket("running", needsUser = false))
        assertEquals(TodayBucket.COMPLETED, todayBucket("delivered", needsUser = false))
    }

    @Test
    fun workFiltersAreDerivedFromAuthoritativeRunState() {
        val approval = MobileWorkRun(
            id = "approval",
            status = "running",
            presentation = MobileWorkPresentation(needsUser = true),
        )
        val active = MobileWorkRun(id = "active", status = "queued")
        val done = MobileWorkRun(id = "done", status = "completed")

        assertTrue(workMatchesFilter(approval, WorkFilter.ATTENTION))
        assertFalse(workMatchesFilter(active, WorkFilter.ATTENTION))
        assertTrue(workMatchesFilter(active, WorkFilter.ACTIVE))
        assertTrue(workMatchesFilter(done, WorkFilter.DONE))
        assertTrue(workMatchesFilter(done, WorkFilter.ALL))
    }
}
