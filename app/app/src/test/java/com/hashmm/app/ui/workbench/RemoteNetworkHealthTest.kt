package com.hashmm.app.ui.remote

import org.junit.Assert.assertEquals
import org.junit.Test

class RemoteNetworkHealthTest {
    @Test fun measuredThresholdsAreDeterministic() {
        assertEquals(RemoteNetworkHealth.UNKNOWN, remoteNetworkHealth(-1, -1.0))
        assertEquals(RemoteNetworkHealth.GOOD, remoteNetworkHealth(45, 0.2))
        assertEquals(RemoteNetworkHealth.FAIR, remoteNetworkHealth(150, 0.4))
        assertEquals(RemoteNetworkHealth.FAIR, remoteNetworkHealth(70, 1.5))
        assertEquals(RemoteNetworkHealth.POOR, remoteNetworkHealth(280, 0.2))
        assertEquals(RemoteNetworkHealth.POOR, remoteNetworkHealth(60, 6.0))
    }
}
