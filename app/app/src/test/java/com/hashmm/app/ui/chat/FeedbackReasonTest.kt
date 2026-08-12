package com.hashmm.app.ui.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FeedbackReasonTest {
    @Test
    fun failureTaxonomyMatchesBackendContract() {
        val codes = feedbackReasons.map { it.first }
        assertEquals(9, codes.distinct().size)
        assertTrue("retrieval_miss" in codes)
        assertTrue("wrong_tool" in codes)
        assertTrue("incomplete" in codes)
        assertTrue("unsafe" in codes)
    }

    @Test
    fun unknownReasonNeverPretendsToBeARealCategory() {
        assertEquals("请选择问题类型", feedbackReasonLabel("not-a-real-code"))
    }
}
