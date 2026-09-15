package com.hashmm.app.ui.tasks

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ComputerTaskPromptTest {
    @Test
    fun browserOptionsBecomeExecutionRequirements() {
        val prompt = buildComputerTaskPrompt(
            kind = "browser",
            task = "调研 RAG Agent",
            primaryOption = "深入",
            secondaryOption = "对比表",
            includeEvidence = true,
        )

        assertTrue(prompt.contains("调研深度=深入"))
        assertTrue(prompt.contains("交付格式=对比表"))
        assertTrue(prompt.contains("来源页面标题、链接和关键证据"))
    }

    @Test
    fun readOnlyTaskForbidsMutation() {
        val prompt = buildComputerTaskPrompt(
            kind = "cu",
            task = "盘点常用目录",
            primaryOption = "常用目录",
            secondaryOption = "",
            includeEvidence = false,
        )

        assertTrue(prompt.contains("仅执行只读检查"))
        assertTrue(prompt.contains("不修改、移动或删除文件"))
        assertFalse(prompt.contains("直接删除"))
    }
}
