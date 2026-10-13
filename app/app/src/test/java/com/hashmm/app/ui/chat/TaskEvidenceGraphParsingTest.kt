package com.hashmm.app.ui.chat

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class TaskEvidenceGraphParsingTest {
    @Test
    fun completionCardReadsDeterministicGraphSummary() {
        val manifest = Json.parseToJsonElement(
            """{
              "handoff": {
                "status": "needs_attention",
                "summary": "已产出结果，但仍有检查未通过。",
                "next_action": "先处理失败检查。",
                "passed_checks": ["output_delivery"],
                "failed_checks": ["artifact_delivery"],
                "not_evaluable_checks": []
              },
              "evidence_graph": {
                "schema": "hashmm.task-evidence-graph.v1",
                "status": "blocked",
                "summary": {"nodes": 9, "edges": 11, "blockers": 2},
                "next_actions": ["重新生成缺失文件"]
              },
              "execution_frontier": {
                "schema": "hashmm.execution-frontier.v1",
                "status": "actionable",
                "summary": {"unresolved": 2, "ready_routes": 1, "scope_blocked": 1},
                "items": [{"minimum_action": "使用 create_document 重新生成报告"}]
              },
              "completion_gate": {
                "schema": "hashmm.completion-gate.v1",
                "status": "incomplete",
                "can_claim_complete": false,
                "summary": {"required": 4, "passed": 2, "failed": 1, "review": 1, "missing": 0},
                "trajectory": {"tool_calls": 3, "agents": 2},
                "next_action": "重新生成并验证报告"
              }
            }""".trimIndent()
        )

        val parsed = parseCompletionSummary(manifest)
        assertNotNull(parsed)
        assertEquals("blocked", parsed!!.graphStatus)
        assertEquals(9, parsed.graphNodes)
        assertEquals(11, parsed.graphEdges)
        assertEquals(2, parsed.graphBlockers)
        assertEquals("重新生成缺失文件", parsed.graphNextAction)
        assertEquals(2, parsed.frontierUnresolved)
        assertEquals(1, parsed.frontierReady)
        assertEquals(1, parsed.frontierScopeBlocked)
        assertEquals("使用 create_document 重新生成报告", parsed.frontierNextAction)
        assertEquals("incomplete", parsed.gateStatus)
        assertEquals(false, parsed.gateCanComplete)
        assertEquals(4, parsed.gateRequired)
        assertEquals(2, parsed.gatePassed)
        assertEquals(1, parsed.gateFailed)
        assertEquals(1, parsed.gateReview)
        assertEquals(3, parsed.trajectoryTools)
        assertEquals(2, parsed.trajectoryAgents)
        assertEquals("重新生成并验证报告", parsed.gateNextAction)
    }
}
