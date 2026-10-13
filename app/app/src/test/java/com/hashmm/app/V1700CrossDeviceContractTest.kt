package com.hashmm.app

import com.hashmm.app.data.remote.parseBootstrapIdentity
import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.ui.chat.drawerConversationGroups
import java.io.File
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class V1700CrossDeviceContractTest {
    private fun source(relative: String): String =
        File(System.getProperty("user.dir"), "src/main/$relative").readText()

    @Test
    fun remoteConfigFailureCannotBecomeSuccessfulCheck() {
        val repository = source("java/com/hashmm/app/data/remote/RemoteConfigRepository.kt")
        assertFalse(repository.contains("finally {"))
        assertTrue(repository.contains("markRemoteConfigChecked(now)"))
        assertTrue(repository.contains("RemoteConfigSyncResult.Failure"))
        assertTrue(repository.indexOf("markRemoteConfigChecked(now)") < repository.indexOf("RemoteConfigSyncResult.Success(url)"))
    }

    @Test
    fun bootstrapCarriesProjectAndSubjectProof() {
        val parsed = parseBootstrapIdentity(
            """{"supabase_project_ref":"project-a","user_sub_fingerprint":"abcdef123456"}""",
        )
        assertEquals("project-a", parsed.first)
        assertEquals("abcdef123456", parsed.second)
    }

    @Test
    fun drawerSeparatesPinnedAndRecentConversations() {
        val now = Instant.now()
        val groups = drawerConversationGroups(listOf(
            ChatConversation(id = "p", title = "Pinned", pinned = true, updatedAt = now.toString()),
            ChatConversation(id = "t", title = "Today", updatedAt = now.minusSeconds(3600).toString()),
            ChatConversation(id = "w", title = "Week", updatedAt = now.minusSeconds(3 * 86400).toString()),
        )).associate { it.first to it.second.map(ChatConversation::id) }
        assertEquals(listOf("p"), groups["置顶"])
        assertEquals(listOf("t"), groups["今天"])
        assertEquals(listOf("w"), groups["过去 7 天"])
    }

    @Test
    fun directModeDoesNotClaimCloudPersistenceWithoutReceipt() {
        val screen = source("java/com/hashmm/app/ui/chat/ChatHomeScreen.kt")
        assertFalse(screen.contains("本轮由手机直连模型回答（已同步云端）"))
        assertTrue(screen.contains("暂未写入服务器"))
        assertTrue(screen.contains("chat.directPersisted"))
    }
}
