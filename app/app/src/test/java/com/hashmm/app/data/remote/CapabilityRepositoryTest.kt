package com.hashmm.app.data.remote

import com.hashmm.app.ui.workbench.capabilityStatusLabel
import com.hashmm.app.ui.workbench.capabilityDetail
import com.hashmm.app.ui.activity.quickActionAvailability
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CapabilityRepositoryTest {
    @Test
    fun parsesOnlyVersionedRuntimeContract() {
        val parsed = parseRuntimeCapabilities(
            """{"contract":"hashmm.runtime-capabilities.v1","truth_contract":"hashmm.capability-truth.v1","revision":"abc","chat_tool_count":42,"ready_count":1,"total_count":1,"capabilities":[{"id":"browser_use","title":"浏览器操作","state":"ready","reason":"ok","enabled":true,"wired":true,"requires_desktop":false,"availability":"available","visibility":"user","diagnostic_only":false,"production_ready":true}]}"""
        )
        assertEquals(42, parsed?.chatToolCount)
        assertEquals("browser_use", parsed?.capabilities?.single()?.id)
        assertTrue(parsed?.capabilities?.single()?.wired == true)
        assertNull(parseRuntimeCapabilities("""{"contract":"unknown","capabilities":[]}"""))
        assertEquals(capabilityCacheScope("https://api.example.com"), capabilityCacheScope(" HTTPS://API.EXAMPLE.COM "))
        assertTrue(capabilityCacheScope("https://api.example.com") != capabilityCacheScope("https://other.example.com"))
    }

    @Test
    fun desktopCapabilityDoesNotPretendReadyWhileComputerIsOffline() {
        val capability = RuntimeCapability(
            id = "computer_use", title = "文件与电脑操作", state = "ready",
            reason = "ok", enabled = true, wired = true, requiresDesktop = true,
            availability = "available", visibility = "user",
            diagnosticOnly = false, productionReady = true,
        )
        assertEquals("等待电脑", capabilityStatusLabel(capability, desktopOnline = false))
        assertEquals("可用", capabilityStatusLabel(capability, desktopOnline = true))
        assertEquals(
            "最近可用",
            capabilityStatusLabel(capability, desktopOnline = true, freshness = "cached"),
        )
        assertTrue(capabilityDetail(capability, "等待电脑").contains("同账号桌面端"))
        assertTrue(capabilityDetail(capability, "可用").contains("确认后执行电脑操作"))
        assertTrue(capabilityDetail(capability, "最近可用").contains("不把缓存当作当前事实"))
        assertEquals(false, quickActionAvailability(listOf(capability), "computer_use", false).enabled)
        assertEquals(true, quickActionAvailability(listOf(capability), "computer_use", true).enabled)
        assertEquals(false, quickActionAvailability(emptyList(), "browser_use", true).enabled)
    }
}
