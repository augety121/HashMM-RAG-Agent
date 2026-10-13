package com.hashmm.app

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class V1800RemoteFabricContractTest {
    private fun source(relative: String): String =
        File(System.getProperty("user.dir"), "src/main/$relative").readText()

    @Test
    fun signalingUsesOneTimeAdmissionAndLeaseHeartbeat() {
        val signaling = source("java/com/hashmm/app/data/remote/RemoteSignalingClient.kt")
        assertTrue(signaling.contains("/api/remote/v4/bootstrap"))
        assertTrue(signaling.contains("/api/remote/v4/socket-ticket"))
        assertTrue(signaling.contains("hashmm.remote-bootstrap.v4"))
        assertTrue(signaling.contains("hashmm.remote.v4"))
        assertFalse(signaling.contains("fun wsUrl(base:"))
        assertTrue(signaling.contains(".put(\"ticket\", socketTicket)"))
        assertTrue(signaling.contains(".put(\"type\", \"heartbeat\")"))
        assertTrue(signaling.contains("\"deviceReplaced\", \"leaseRejected\""))
        assertFalse(signaling.contains("rst_")) // no embedded admission credential
    }

    @Test
    fun remoteIdentityIsAppOwnedAndDiagnosticsAreVisible() {
        val model = source("java/com/hashmm/app/ui/remote/RemoteControlViewModel.kt")
        val screen = source("java/com/hashmm/app/ui/remote/RemoteControlScreen.kt")
        assertTrue(model.contains("settings.clientInstanceId()"))
        assertFalse(model.contains("Settings.Secure.ANDROID_ID"))
        assertTrue(model.contains("ownerFingerprint"))
        assertTrue(model.contains("连接超时：公网入口可达，但设备信令未完成注册"))
        assertTrue(screen.contains("账号通道已连接，未发现可远程电脑"))
        assertTrue(screen.contains("devices.any { it.remoteReady }"))
        assertFalse(screen.contains("Text(\"V2\""))
        assertTrue(screen.contains("设备列表已同步"))
    }
}
