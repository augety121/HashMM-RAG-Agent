package com.hashmm.app

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class V2000RemoteGenerationContractTest {
    private fun source(relative: String): String =
        File(System.getProperty("user.dir"), "src/main/$relative").readText()

    @Test
    fun staleSocketCallbacksAreFencedAndRetriesPreserveBudget() {
        val client = source("java/com/hashmm/app/data/remote/RemoteSignalingClient.kt")
        val vm = source("java/com/hashmm/app/ui/remote/RemoteControlViewModel.kt")
        assertTrue(client.contains("connectionGeneration.incrementAndGet()"))
        assertTrue(client.contains("generation != connectionGeneration.get()"))
        assertTrue(client.contains("remoteReady: Boolean = false"))
        assertTrue(vm.contains("connectInternal(resetAttempts = false)"))
        assertFalse(vm.contains("signaling = null\n            connect()"))
    }
}
