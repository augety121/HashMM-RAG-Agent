package com.hashmm.app.ui.workbench

import com.hashmm.app.data.remote.QualityData
import com.hashmm.app.data.remote.ToolQualityData
import com.hashmm.app.data.remote.ExecutionFrontierQualityData
import com.hashmm.app.data.remote.CompletionGateQualityData
import com.hashmm.app.data.remote.MobileWorkEvent
import org.junit.Assert.assertEquals
import org.junit.Test
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject

class WorkbenchPresentationTest {
    @Test
    fun runStatus_usesProductLanguageAndSemanticTone() {
        assertEquals("检查通过", runStatusLabel("checks_passed"))
        assertEquals(KitTone.Success, runTone("checks_passed"))
        assertEquals("有限交付", runStatusLabel("delivered_with_limits"))
        assertEquals(KitTone.Warn, runTone("delivered_with_limits"))
        assertEquals("已阻塞", runStatusLabel("blocked"))
        assertEquals(KitTone.Error, runTone("blocked"))
    }

    @Test
    fun runDuration_doesNotInventMissingTiming() {
        assertEquals("未记录", formatRunDuration(0))
        assertEquals("850 ms", formatRunDuration(850))
        assertEquals("1.5 秒", formatRunDuration(1_500))
        assertEquals("2 分 5 秒", formatRunDuration(125_000))
    }

    @Test
    fun retrievalRun_readsOnlyTheSharedEvidenceContract() {
        val snapshot = Json.parseToJsonElement("""{
          "run_manifest":{"retrieval":{"run":{
            "schema":"hashmm.retrieval-run.v1","status":"degraded",
            "resolved_mode":"mix","evidence_count":3,"total_candidates":11,
            "attempts":[{},{}],"degradations":[{}],"elapsed_ms":825,
            "acl_scoped":true,"expansions":[{"stage":"knowledge_graph","added":2,"skipped_missing":1,"skipped_forbidden":1}]
          }}}
        }""").jsonObject
        val result = mobileRetrievalSummary(snapshot)!!
        assertEquals("降级完成", result.statusLabel)
        assertEquals("mix", result.route)
        assertEquals(3, result.evidenceCount)
        assertEquals(11, result.totalCandidates)
        assertEquals(2, result.attempts)
        assertEquals(1, result.degradations)
        assertEquals(true, result.aclScoped)
        assertEquals(2, result.graphAdded)
        assertEquals(2, result.graphSkipped)
        assertEquals(null, mobileRetrievalSummary(Json.parseToJsonElement("{}") .jsonObject))
    }

    @Test
    fun agentSessionEvent_usesRealLifecyclePayload() {
        val payload = Json.parseToJsonElement(
            """{"role":"研究员","session_status":"completed","tool_calls":2,"allowed_tools":["kb_search","web_search"]}"""
        ).jsonObject
        val detail = mobileAgentSessionDetail(MobileWorkEvent(type = "agent_session", payload = payload))
        assertEquals("研究员 · 完成 · 2 次工具调用 · 2 项授权能力", detail)
        assertEquals("", mobileAgentSessionDetail(MobileWorkEvent(type = "progress", payload = payload)))
    }

    @Test
    fun qualityVerdict_preservesNotEvaluableAndFindsReviewItems() {
        assertEquals("waiting", qualityVerdict(QualityData(samples = 0)).code)
        assertEquals(
            "waiting",
            qualityVerdict(QualityData(samples = 4, avgGroundedRatio = null)).code,
        )
        assertEquals(
            "review",
            qualityVerdict(QualityData(
                samples = 8,
                evaluableSamples = 8,
                avgGroundedRatio = 0.75,
                answersWithoutSources = 1,
            )).code,
        )
        assertEquals(
            "review",
            qualityVerdict(QualityData(
                completionGateQuality = CompletionGateQualityData(unsafeCompletionClaims = 1),
            )).code,
        )
        assertEquals(
            "healthy",
            qualityVerdict(QualityData(
                samples = 8,
                evaluableSamples = 8,
                avgGroundedRatio = 0.9,
            )).code,
        )
        assertEquals(
            "review",
            qualityVerdict(QualityData(
                samples = 8,
                evaluableSamples = 8,
                avgGroundedRatio = 0.95,
                executionFrontierQuality = ExecutionFrontierQualityData(scopeBlockedItems = 2),
            )).code,
        )
        assertEquals(
            "review",
            qualityVerdict(QualityData(
                samples = 8,
                evaluableSamples = 8,
                avgGroundedRatio = 0.95,
                toolQuality = ToolQualityData(evaluableRuns = 2, failedRuns = 1),
            )).code,
        )
        assertEquals(
            "healthy",
            qualityVerdict(QualityData(
                toolQuality = ToolQualityData(
                    runManifests = 2,
                    evaluableRuns = 2,
                    executionSuccessRate = 1.0,
                ),
            )).code,
        )
    }
}
