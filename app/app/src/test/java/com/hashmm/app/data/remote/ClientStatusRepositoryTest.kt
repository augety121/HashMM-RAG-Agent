package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Test

class ClientStatusRepositoryTest {
    @Test
    fun desktopCountUsesAuthenticatedRunnerPresenceNotRemoteHosts() {
        val payload = """
            {"runners":[
              {"device_id":"pc-a","runner":"desktop","online":true},
              {"device_id":"pc-b","runner":"desktop","online":false},
              {"device_id":"worker-a","runner":"server","online":true}
            ]}
        """.trimIndent()

        assertEquals(1, parseDesktopRunnerCount(payload))
    }

    @Test
    fun malformedRunnerPayloadFailsClosed() {
        assertEquals(0, parseDesktopRunnerCount("not-json"))
        assertEquals(0, parseDesktopRunnerCount("{\"runners\":[]}"))
    }

    @Test
    fun unifiedProjectionCountsDevicesAndRemoteReadiness() {
        val payload = """
            {"desktop_count":2,"devices":[
              {"device_id":"pc-a","online":true,"remote_ready":true},
              {"device_id":"pc-b","online":true,"remote_ready":false}
            ]}
        """.trimIndent()

        assertEquals(2 to 1, parseUnifiedDeviceStatus(payload))
    }

    @Test
    fun unifiedProjectionFailsClosed() {
        assertEquals(0 to 0, parseUnifiedDeviceStatus("not-json"))
    }
}
