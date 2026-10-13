package com.hashmm.app

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class V1500SecurityContractTest {
    private fun source(relative: String): String =
        File(System.getProperty("user.dir"), "src/main/$relative").readText()

    @Test
    fun secureReplicaUsesKeystoreNoBackupAndAtomicReplace() {
        val cache = source("java/com/hashmm/app/data/cache/SecureCache.kt")
        assertTrue(cache.contains("AndroidKeyStore"))
        assertTrue(cache.contains("noBackupFilesDir"))
        assertTrue(cache.contains("ATOMIC_MOVE"))
        assertTrue(cache.contains("updateAAD"))
        assertFalse(cache.contains("if (file.exists()) file.delete()"))
    }

    @Test
    fun remoteConfigIsNotPolledEveryMinute() {
        val main = source("java/com/hashmm/app/ui/MainViewModel.kt")
        assertTrue(main.contains("6 * 60 * 60 * 1000L"))
        assertFalse(main.contains("delay(60_000L)"))
    }

    @Test
    fun todayAndWorkNavigationIconsHaveNoDecorativeTile() {
        val today = source("java/com/hashmm/app/ui/activity/ActivityScreen.kt")
        val work = source("java/com/hashmm/app/ui/workbench/WorkbenchHubScreen.kt")
        assertFalse(today.contains("Modifier.size(38.dp).background(cs.surface"))
        assertTrue(today.contains("Icons.Outlined.Schedule, null, modifier = Modifier.size(24.dp)"))
        assertTrue(work.contains("ToolRow(Icons.Outlined.History, \"全部记录\""))
    }

    @Test
    fun workCanvasDoesNotConflateOfflineMissingAndForbidden() {
        val repository = source("java/com/hashmm/app/data/remote/WorkRuntimeRepository.kt")
        val screen = source("java/com/hashmm/app/ui/workbench/WorkCanvasScreen.kt")
        val auth = source("java/com/hashmm/app/data/auth/AuthRepository.kt")
        assertTrue(repository.contains("当前账号无权查看这项工作"))
        assertTrue(repository.contains("这项工作不存在或已归档"))
        assertTrue(repository.contains("无法连接 HashMM 服务器"))
        assertTrue(repository.contains("登录状态已过期，请重新登录"))
        assertTrue(auth.contains("refreshCurrentSession()"))
        assertFalse(screen.contains("没有找到这项工作，或当前账号无权查看"))
    }
}
