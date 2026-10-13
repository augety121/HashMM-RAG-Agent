package com.hashmm.app

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class V2200DirectFirstRemoteContractTest {
    private fun source(relative: String): String =
        File(System.getProperty("user.dir"), "src/main/$relative").readText()

    @Test
    fun turnPreferenceAndLegacyHttpFallbackAreSeparate() {
        val signaling = source("java/com/hashmm/app/data/remote/RemoteSignalingClient.kt")
        val model = source("java/com/hashmm/app/ui/remote/RemoteControlViewModel.kt")
        assertTrue(signaling.contains("transportMode"))
        assertTrue(signaling.contains("legacyRelayAfterMs"))
        assertTrue(model.contains("legacyRelayActive"))
        assertTrue(model.contains("IceTransportsType.RELAY"))
        assertTrue(model.contains("delay(legacyRelayAfterMs)"))
        assertFalse(model.contains("if (on) startRelayPoll() else stopRelayPoll()"))
    }
}
