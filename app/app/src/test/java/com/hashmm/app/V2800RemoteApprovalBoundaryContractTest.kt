package com.hashmm.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class V2800RemoteApprovalBoundaryContractTest {
    private val root = File(requireNotNull(System.getProperty("user.dir")))

    @Test
    fun mediaFallbackStartsOnlyAfterAuthorisedReady() {
        val source = File(root, "src/main/java/com/hashmm/app/ui/remote/RemoteControlViewModel.kt").readText()
        val select = source.substringAfter("fun selectDevice").substringBefore("fun sendInput")
        val pending = source.substringAfter("override fun onPermissionPending").substringBefore("override fun onReady")
        val ready = source.substringAfter("override fun onReady").substringBefore("override fun onRemoteTicket")
        assertFalse(select.contains("armNegotiateTimeout()"))
        assertFalse(pending.contains("armNegotiateTimeout()"))
        assertTrue(pending.contains("RemoteStage.APPROVAL_PENDING"))
        assertTrue(ready.contains("ticket.isBlank()"))
        assertTrue(ready.contains("armNegotiateTimeout()"))
    }

    @Test
    fun signalingRejectsReadyWithoutViewerTicket() {
        val source = File(root, "src/main/java/com/hashmm/app/data/remote/RemoteSignalingClient.kt").readText()
        val ready = source.substringAfter("\"ready\" ->").substringBefore("\"ticket\" ->")
        assertTrue(ready.contains("nextTicket.isBlank()"))
        assertTrue(ready.contains("ticket_issue_failed"))
    }

    @Test
    fun ordinaryUserModelSettingsExposeAuthorisedSub2Api() {
        val source = File(root, "src/main/java/com/hashmm/app/ui/models/ModelConfigViewModel.kt").readText()
        assertTrue(source.contains("ModelProviderInfo(\"sub2api\""))
        assertTrue(source.contains("chat_completions\", \"responses"))
    }

    @Test
    fun remoteControlRefreshesSupabaseSessionOnceAfterControlPlane401() {
        val source = File(root, "src/main/java/com/hashmm/app/ui/remote/RemoteControlViewModel.kt").readText()
        val closed = source.substringAfter("override fun onClosed").substringBefore("// ── WebRTC")
        assertTrue(closed.contains("REMOTE_BOOTSTRAP_401"))
        assertTrue(closed.contains("refreshCurrentSession()"))
        assertTrue(closed.contains("!authRefreshAttempted"))
    }
}
