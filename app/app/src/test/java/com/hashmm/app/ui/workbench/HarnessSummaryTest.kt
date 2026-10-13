package com.hashmm.app.ui.workbench

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class HarnessSummaryTest {
    @Test
    fun readsSameBoundedHarnessAsDesktop() {
        val snapshot = Json.parseToJsonElement(
            """{
              "run_manifest": {
                "harness": {
                  "schema": "hashmm.agent-harness.v1",
                  "context": {"approval_mode":"read_only","network_mode":"deny"},
                  "capabilities": {
                    "declared_count": 8,
                    "effective_count": 6,
                    "missing_executors": ["ghost"]
                  },
                  "trajectory": {
                    "event_count": 12,
                    "event_types": {"tool_finished":3,"context_compaction":1}
                  },
                  "children": {"total":2,"limit":3},
                  "terminal": {"reason":"completed"}
                },
                "context_lifecycle": {
                  "schema":"hashmm.context-lifecycle.v1",
                  "generation":2,
                  "checkpoint_id":"ctx-checkpoint-2"
                }
              }
            }""".trimIndent()
        ).jsonObject

        val summary = mobileHarnessSummary(snapshot)
        assertNotNull(summary)
        assertEquals("completed", summary!!.terminalReason)
        assertEquals(6, summary.effectiveTools)
        assertEquals(8, summary.declaredTools)
        assertEquals(1, summary.missingExecutors)
        assertEquals(12, summary.events)
        assertEquals(3, summary.toolCalls)
        assertEquals(1, summary.compactions)
        assertEquals(2, summary.children)
        assertEquals(3, summary.childLimit)
        assertEquals("read_only", summary.approvalMode)
        assertEquals("deny", summary.networkMode)
        assertEquals(2, summary.contextGeneration)
        assertEquals(true, summary.contextCheckpointed)
    }
}
