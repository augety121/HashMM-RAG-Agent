package com.hashmm.app.ui.chat

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ToolApprovalParsingTest {
    @Test
    fun parsesDurableApprovalFromRunManifest() {
        val manifest = Json.parseToJsonElement(
            """{
              "approval_request": {
                "schema": "hashmm.tool-approval.v1",
                "request_id": "approval-1",
                "tool_name": "run_shell",
                "arguments": {"command": "echo safe", "api_key": "[已隐藏敏感值]"},
                "risk": "system",
                "status": "pending"
              }
            }""".trimIndent()
        )

        val parsed = parseToolApproval(manifest)!!
        assertEquals("approval-1", parsed.requestId)
        assertEquals("run_shell", parsed.toolName)
        assertEquals("system", parsed.risk)
        assertEquals("pending", parsed.status)
        assertTrue(parsed.arguments.contains("[已隐藏敏感值]"))
    }

    @Test
    fun rejectsIncompleteApprovalContract() {
        assertNull(parseToolApproval(Json.parseToJsonElement("""{"approval_request":{"status":"pending"}}""")))
    }
}
