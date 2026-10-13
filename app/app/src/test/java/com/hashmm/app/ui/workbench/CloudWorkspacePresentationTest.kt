package com.hashmm.app.ui.workbench

import org.junit.Assert.assertEquals
import org.junit.Test

class CloudWorkspacePresentationTest {
    @Test
    fun desktopAndCloudExecutionRemainDistinct() {
        assertEquals("电脑在线", workConnectionLabel(false, true, 1, true, true))
        assertEquals("云环境可用", workConnectionLabel(false, false, 0, true, true))
        assertEquals("等待电脑", workConnectionLabel(false, false, 0, false, true))
        assertEquals("离线", workConnectionLabel(false, false, 0, false, false))
    }
}
