package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import com.hashmm.app.data.remote.AdminToolsRepository
import com.hashmm.app.data.remote.AdvancedData
import com.hashmm.app.data.remote.AuditItem
import com.hashmm.app.data.remote.QualityData
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject

/** V219: 审计/质量/高级能力三原生页的共用取数壳。 */
@HiltViewModel
class AdminToolsViewModel @Inject constructor(private val repo: AdminToolsRepository) : ViewModel() {
    suspend fun audit(): Pair<List<AuditItem>, String?> = repo.audit()
    suspend fun quality(): QualityData = repo.quality()
    suspend fun advanced(): AdvancedData = repo.advanced()
}
