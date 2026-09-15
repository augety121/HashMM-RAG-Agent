package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import com.hashmm.app.data.remote.FeedData
import com.hashmm.app.data.remote.FeedRepository
import com.hashmm.app.data.remote.MobileWorkRun
import com.hashmm.app.data.remote.MobileWorkCommandOutcome
import com.hashmm.app.data.remote.MobileWorkCanvas
import com.hashmm.app.data.remote.MobileWorkCanvasLookup
import com.hashmm.app.data.remote.MobileWorkDecisionOutcome
import com.hashmm.app.data.remote.MobileWorkAnnotationOutcome
import com.hashmm.app.data.remote.MobileExecutionDevice
import com.hashmm.app.data.remote.MobilePlacementOutcome
import com.hashmm.app.data.remote.MobileWorkflowOutcome
import com.hashmm.app.data.remote.MobileWorkSnapshot
import com.hashmm.app.data.remote.ChatLiveRepository
import com.hashmm.app.data.remote.ScheduledData
import com.hashmm.app.data.remote.ScheduledRepository
import com.hashmm.app.data.remote.WorkRuntimeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject

/** V218: 原生「定时任务/运行轨迹」页的共用取数（一次 /api/feed）。 */
@HiltViewModel
class NativeFeedViewModel @Inject constructor(
    private val repo: FeedRepository,
    private val scheduled: ScheduledRepository,
    private val workRuntime: WorkRuntimeRepository,
    private val chatLive: ChatLiveRepository,
) : ViewModel() {
    suspend fun load(): FeedData = repo.feed()
    suspend fun health(): Pair<Boolean, String> = repo.health()
    suspend fun scheduled(): ScheduledData = scheduled.list()
    suspend fun workRuns(force: Boolean = false): MobileWorkSnapshot = workRuntime.fetch(force)
    suspend fun workRunDetail(id: String, force: Boolean = false): MobileWorkRun? =
        workRuntime.detail(id, force)
    suspend fun workCanvas(id: String, force: Boolean = false): MobileWorkCanvas? =
        workRuntime.workspace(id, force)
    suspend fun workCanvasResult(id: String, force: Boolean = false): MobileWorkCanvasLookup =
        workRuntime.workspaceResult(id, force)
    suspend fun workRunCommand(id: String, action: String, expectedRevision: Int): MobileWorkCommandOutcome =
        workRuntime.command(id, action, expectedRevision)
    suspend fun workRunDecision(
        id: String,
        action: String,
        expectedRevision: Int,
        note: String = "",
    ): MobileWorkDecisionOutcome = workRuntime.decision(id, action, expectedRevision, note)
    suspend fun workRunAnnotation(
        id: String,
        artifactId: String,
        artifactRevision: Int,
        note: String,
        expectedRevision: Int,
    ): MobileWorkAnnotationOutcome =
        workRuntime.annotate(id, artifactId, artifactRevision, note, expectedRevision)
    suspend fun executionDevices(): List<MobileExecutionDevice> =
        workRuntime.executionDevices()
    suspend fun requestWorkPlacement(
        id: String,
        deviceId: String,
        expectedRevision: Int,
    ): MobilePlacementOutcome =
        workRuntime.requestPlacement(id, deviceId, expectedRevision)
    suspend fun publishWorkflow(id: String, expectedRevision: Int): MobileWorkflowOutcome =
        workRuntime.publishWorkflow(id, expectedRevision)
    suspend fun workResultViewUrl(downloadUrl: String, filename: String): String? =
        chatLive.fileViewUrl(downloadUrl, filename)
    suspend fun createScheduled(action: String, name: String, hours: Int, convId: String): String? =
        scheduled.create(action, name, hours, convId)
    suspend fun runScheduled(id: String): String? = scheduled.runNow(id)
    suspend fun toggleScheduled(id: String, enabled: Boolean): String? = scheduled.toggle(id, enabled)
    suspend fun deleteScheduled(id: String): String? = scheduled.delete(id)
}
