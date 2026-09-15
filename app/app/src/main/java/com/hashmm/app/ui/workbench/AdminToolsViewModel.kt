package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import com.hashmm.app.data.remote.AdminToolsRepository
import com.hashmm.app.data.remote.AdvancedData
import com.hashmm.app.data.remote.AuditItem
import com.hashmm.app.data.remote.NotifData
import com.hashmm.app.data.remote.QualityData
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject

/** V219: 审计/质量/高级能力三原生页的共用取数壳。 */
@HiltViewModel
class AdminToolsViewModel @Inject constructor(private val repo: AdminToolsRepository) : ViewModel() {
    suspend fun audit(): Pair<List<AuditItem>, String?> = repo.audit()
    suspend fun auditQuery(username: String, startTs: Long): Pair<List<AuditItem>, String?> = repo.auditQuery(username, startTs)
    suspend fun notifications(): NotifData = repo.notifications()
    suspend fun notificationsReadAll(): Boolean = repo.notificationsReadAll()
    suspend fun viewerUrl(shareId: String): String? = repo.viewerUrl(shareId)
    suspend fun quality(): QualityData = repo.quality()
    suspend fun advanced(): AdvancedData = repo.advanced()
    suspend fun dispatch(kind: String, payload: org.json.JSONObject): Pair<Boolean, String> = repo.createDispatch(kind, payload)
    // V267 测试中枢（对标桌面端）
    suspend fun selftestSuites(): Pair<List<AdminToolsRepository.SelfTestSuite>, String?> = repo.selftestSuites()
    suspend fun selftestRun(ids: List<String>): AdminToolsRepository.SelfTestRun = repo.selftestRun(ids)
    suspend fun saveSelftestReport(md: String): Pair<Boolean, String?> = repo.saveSelftestReport(md)
    // V268 上下文透视
    suspend fun contextInspect(convId: String = ""): AdminToolsRepository.CtxInspect = repo.contextInspect(convId)
    suspend fun contextCompact(convId: String): Pair<Boolean, String> = repo.contextCompact(convId)
}
