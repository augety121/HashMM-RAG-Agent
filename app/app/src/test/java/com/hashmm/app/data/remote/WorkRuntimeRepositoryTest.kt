package com.hashmm.app.data.remote

import com.hashmm.app.ui.workbench.mobileWorkStatus
import com.hashmm.app.ui.workbench.mobileWorkAction
import com.hashmm.app.ui.workbench.mobileWorkBoundary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WorkRuntimeRepositoryTest {
    @Test
    fun `work outbox keeps stable ids and uses bounded reconciliation backoff`() {
        val first = MobileWorkOutboxItem(
            id = "app-stable-1",
            kind = "command",
            runId = "wr1",
            action = "cancel",
            expectedRevision = 3,
            createdAt = 1,
        )
        val updated = first.copy(attempts = 1)
        val once = mobileWorkOutboxUpsert(MobileWorkOutbox(), first)
        val replay = mobileWorkOutboxUpsert(once, updated)
        assertEquals(1, replay.items.size)
        assertEquals("app-stable-1", replay.items.single().id)
        assertEquals(1, replay.items.single().attempts)
        assertEquals(10_000L, mobileWorkOutboxBackoffMs(1))
        assertEquals(15 * 60_000L, mobileWorkOutboxBackoffMs(20))
        assertTrue(mobileWorkOutboxRemove(replay, "app-stable-1").items.isEmpty())
    }

    @Test
    fun `in-memory work cache is isolated when account changes`() {
        assertFalse(workMemoryOwnerChanged("user-a", "user-a"))
        assertTrue(workMemoryOwnerChanged("user-a", "user-b"))
        assertTrue(workMemoryOwnerChanged(null, "user-a"))
    }

    @Test
    fun `parses shared work feed contract and cursors`() {
        val raw = """
            {
              "schema":"hashmm.work-feed.v1",
              "next_cursor":42,
              "high_water_cursor":44,
              "has_more":true,
              "items":[{
                "schema":"hashmm.work-run.v1","id":"wr1","conversation_id":"c1",
                "kind":"team","title":"整理调研报告","status":"running",
                "revision":3,"event_cursor":8,"change_cursor":42,
                "control":{"schema":"hashmm.work-control.v1","expected_revision":3,
                  "available_actions":["cancel"],"side_effect_boundary":"cooperative_after_current_model_call"},
                "snapshot":{"last_event":"agent"},"created_at":1,"updated_at":2
              }]
            }
        """.trimIndent()
        val feed = parseMobileWorkFeed(raw)!!
        assertEquals(42L, feed.nextCursor)
        assertEquals(44L, feed.highWaterCursor)
        assertEquals("wr1", feed.items.single().id)
        assertEquals("team", feed.items.single().kind)
        assertEquals(8, feed.items.single().eventCursor)
        assertEquals(listOf("cancel"), feed.items.single().control.availableActions)
        assertEquals(3, feed.items.single().control.expectedRevision)
    }

    @Test
    fun `workspace v2 stream keeps canonical state and trust boundaries`() {
        val raw = """
            {
              "schema":"hashmm.workspace.v2",
              "today":{"needs_user":[{
                "schema":"hashmm.workspace-run.v2","id":"run-review",
                "workspace_id":"personal","thread_id":"conv-1","kind":"artifact",
                "state":"review","legacy_status":"delivered","revision":4,
                "change_cursor":12,"title":"检查交付","current_step":"等待验收",
                "next_action":"检查并决定","needs_user":true,
                "evidence":{"summary":{"evidence_count":3}},
                "artifacts":[],"available_commands":["accept_delivery"],
                "created_at":1,"updated_at":2
              }],"in_progress":[],"recent_results":[]},
              "runs":[{
                "schema":"hashmm.workspace-run.v2","id":"run-review",
                "workspace_id":"personal","thread_id":"conv-1","kind":"artifact",
                "state":"review","legacy_status":"delivered","revision":4,
                "change_cursor":12,"title":"检查交付","current_step":"等待验收",
                "next_action":"检查并决定","needs_user":true,
                "evidence":{"summary":{"evidence_count":3}},
                "artifacts":[],"available_commands":["accept_delivery"],
                "created_at":1,"updated_at":2
              }],
              "sync":{"next_cursor":12,"high_water_cursor":12},
              "trust":{"owner_isolation":true,"model_prose_is_execution_evidence":false}
            }
        """.trimIndent()
        val feed = requireNotNull(parseMobileWorkspaceFeed(raw))
        assertEquals(12L, feed.nextCursor)
        assertEquals("delivered", feed.items.single().status)
        assertEquals("等待验收", feed.items.single().presentation.statusLabel)
        assertEquals(3, feed.items.single().presentation.evidence.count)
        assertEquals("delivery", feed.actionInbox.items.single().type)
        assertEquals("blocked", canonicalWorkStatus("future-provider-state"))
        assertEquals("observed", canonicalWorkStatus("observed"))

        assertNull(
            parseMobileWorkspaceFeed(
                raw.replace(
                    "\"model_prose_is_execution_evidence\":false",
                    "\"model_prose_is_execution_evidence\":true",
                ),
            ),
        )
    }

    @Test
    fun `rejects incompatible contract and exposes user statuses`() {
        assertNull(parseMobileWorkFeed("{\"schema\":\"demo\",\"items\":[]}"))
        assertEquals("执行中", mobileWorkStatus("running"))
        assertEquals("已验证完成", mobileWorkStatus("completed"))
        assertEquals("状态未知", mobileWorkStatus("made_up"))
    }

    @Test
    fun `parses bounded incremental event detail`() {
        val detail = parseMobileWorkRun(
            """{
              "schema":"hashmm.work-run.v1","id":"wr-detail","kind":"loop",
              "title":"整理资料","status":"waiting_approval","revision":5,
              "event_cursor":9,"change_cursor":61,"events_truncated":false,
              "active_generation_id":"wg1",
              "active_generation":{"schema":"hashmm.work-generation.v1","id":"wg1",
                "run_id":"wr-detail","generation":2,"status":"active","manifest_hash":"abc"},
              "artifact_revisions":[{"schema":"hashmm.artifact-revision.v1","id":"ar1",
                "artifact_id":"report","run_id":"wr-detail","revision":2,
                "content_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "media_type":"application/pdf","size_bytes":10,"verification":"verified"}],
              "snapshot":{"last_event":"approval_request"},
              "events":[
                {"schema":"hashmm.work-event.v1","id":"e8","run_id":"wr-detail","seq":8,
                 "type":"artifact","status":"running","summary":"产物已生成","payload":{},"created_at":3},
                {"schema":"hashmm.work-event.v1","id":"e9","run_id":"wr-detail","seq":9,
                 "type":"approval_request","status":"waiting_approval","summary":"等待确认","payload":{},"created_at":4}
              ]
            }"""
        )!!
        assertEquals("wr-detail", detail.id)
        assertEquals(9, detail.eventCursor)
        assertEquals(listOf(8, 9), detail.events.map { it.seq })
        assertEquals("等待确认", detail.events.last().summary)
        assertEquals("wg1", detail.activeGenerationId)
        assertEquals(2, detail.activeGeneration?.generation)
        assertEquals("verified", detail.artifactRevisions.single().verification)
    }

    @Test
    fun `maps real controls and stale revision errors for native UI`() {
        assertEquals("暂停并保存检查点", mobileWorkAction("pause"))
        assertEquals("从检查点继续", mobileWorkAction("resume"))
        assertEquals("仅桌面端领取前可取消", mobileWorkBoundary("cancel_before_desktop_claim_only"))
        assertEquals("任务状态已经变化（当前 r9），请刷新后再操作", mobileControlError("revision_conflict", 9))
        assertEquals("桌面端已经领取或完成任务，无法再撤回", mobileControlError("already_claimed_or_finished", null))
    }

    @Test
    fun `parses governed work canvas without enabling automatic execution`() {
        val canvas = parseMobileWorkCanvas(
            """{
              "schema":"hashmm.work-canvas.v1",
              "run_id":"wr-canvas","conversation_id":"c1",
              "overview":{"title":"整理报告","goal":"形成可核验交付",
                "progress":{"mode":"criteria","completed":1,"total":2,"label":"1/2"}},
              "process":{"stages":[{"id":"s1","order":1,"label":"读取资料","status":"done"}],
                "event_count":4},
              "evidence":{"status":"valid","summary":{"sources":2,"checks":1,"receipts":1}},
              "results":[{"schema":"hashmm.work-result.v1","id":"r1","name":"report.docx",
                "kind":"document","version":2,"version_ref":"v2","verification":"ready",
                "download_url":"/api/conversations/c1/download/report.docx"}],
              "completion_receipt":{"schema":"hashmm.completion-receipt.v1",
                "receipt_id":"done1","run_id":"wr-canvas","status":"awaiting_review",
                "summary":{"results":1,"sources":2,"checks":1}},
              "next_actions":{"schema":"hashmm.governed-next-actions.v1",
                "items":[{"id":"n1","kind":"review","label":"检查并验收成果",
                  "within_scope":true,"requires_confirmation":true,"auto_execute":false}],
                "governance":{"auto_execution_enabled":false,"widens_scope":false}},
              "learning":{"schema":"hashmm.skill-learning-projection.v1",
                "used_versions":[{"id":"s1","name":"证据审阅","scope":"personal",
                  "version_ref":"skill:s1:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                  "status":"used"}],
                "governance":{"records_exact_version":true,"prompt_body_exposed":false,
                  "automatic_promotion_allowed":false,"owner_bound_feedback_required":true,
                  "paired_replay_required_before_promotion":true,
                  "safety_regression_blocks_promotion":true}},
              "operating":{"schema":"hashmm.user-operating-projection.v1",
                "method_label":"深度工作","route_state":"ready","route_label":"已准备",
                "reason":"运行时已确认","requires_confirmation":true,
                "can_resume":true,"evidence_required":true,"revision":"abc123"},
              "sync":{"revision":7,"event_cursor":4,"change_cursor":9,"etag":"wc1",
                "active_generation_id":"wg1","generation_hash":"hash1"},
              "integrity":{"projection_only":true,"auto_executes":false,"widens_scope":false,
                "model_prose_is_evidence":false,"owner_check_required_by_api":true}
            }"""
        )!!
        assertEquals("wr-canvas", canvas.runId)
        assertEquals("report.docx", canvas.results.single().name)
        assertEquals("awaiting_review", canvas.completionReceipt.status)
        assertFalse(canvas.nextActions.items.single().autoExecute)
        assertFalse(canvas.nextActions.governance.autoExecutionEnabled)
        assertEquals("证据审阅", canvas.learning.usedVersions.single().name)
        assertFalse(canvas.learning.governance.automaticPromotionAllowed)
        assertEquals("深度工作", canvas.operating.methodLabel)
        assertTrue(canvas.operating.canResume)
        assertTrue(canvas.operating.evidenceRequired)
        assertEquals("wg1", canvas.sync.activeGenerationId)
    }

    @Test
    fun `rejects unsafe canvas projection and maps verification decision errors`() {
        assertNull(
            parseMobileWorkCanvas(
                """{"schema":"hashmm.work-canvas.v1","run_id":"wr1",
                  "integrity":{"projection_only":true,"auto_executes":true,"widens_scope":false}}"""
            )
        )
        assertEquals(
            "来源已变化",
            mobileDecisionError("verification_blocked", "来源已变化", null),
        )
        assertEquals(
            "工作状态已经变化（当前 r8），请刷新后再验收",
            mobileDecisionError("revision_conflict", null, 8),
        )
    }

    @Test
    fun `parses shared work os projection without promoting unverified autonomy`() {
        val canvas = parseMobileWorkCanvas(
            """{
              "schema":"hashmm.work-canvas.v1","run_id":"wr-v458",
              "product":{
                "schema":"hashmm.work-projection.v2",
                "identity":{"work_id":"wr-v458","project_id":"project-1","conversation_id":"c1"},
                "contract":{"goal":"形成可核验报告","constraints":["不得省略来源"],
                  "completion_criteria":[{"id":"c1","label":"关键结论有依据","required":true}]},
                "placement":{"kind":"remote_device","label":"办公室电脑","state":"ready"},
                "capability_plan":{"selected":"browser","label":"需要打开浏览器",
                  "reason":"浏览器已就绪","requires_confirmation":false},
                "work_twin":{"schema":"hashmm.work-twin.v1",
                  "summary":{"goals":1,"criteria":1,"tasks":3,"evidence":4,"artifacts":1,"stale":0}},
                "recovery":{"schema":"hashmm.recovery-center.v1",
                  "conversation":{"available":true,"checkpoint_count":1},
                  "files":{"available":true,"previous_versions":2},
                  "execution":{"available":false,"checkpoint_count":0}},
                "collaboration":{"schema":"hashmm.agent-collaboration.v1",
                  "user_summary":"本次工作由一个执行链完成",
                  "write_isolation":{"status":"not_proven"}},
                "autonomy":{"schema":"hashmm.autonomy-profile.v1",
                  "released_level":0,"label":"回答与建议","evaluation_receipt_valid":false},
                "workflow_candidate":{"status":"evidence_required","can_publish":false,
                  "required_next_step":"先完成一次有执行凭证的演示和回放验证"}
              },
              "learning":{"governance":{"automatic_promotion_allowed":false,"prompt_body_exposed":false}},
              "integrity":{"projection_only":true,"auto_executes":false,"widens_scope":false}
            }"""
        )!!
        val product = requireNotNull(canvas.product)
        assertEquals("project-1", product.identity.projectId)
        assertEquals("办公室电脑", product.placement.label)
        assertEquals(4, product.workTwin.summary.evidence)
        assertEquals(2, product.recovery.files.previousVersions)
        assertEquals(0, product.autonomy.releasedLevel)
        assertFalse(product.autonomy.evaluationReceiptValid)
        assertFalse(product.workflowCandidate.canPublish)
    }
}
