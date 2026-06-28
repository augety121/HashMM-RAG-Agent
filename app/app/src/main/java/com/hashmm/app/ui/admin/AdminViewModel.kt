package com.hashmm.app.ui.admin

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.AdminRepository
import com.hashmm.app.data.remote.AdminUser
import com.hashmm.app.data.remote.AuditEntry
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class AdminTab { USERS, AUDIT }

data class AdminUi(
    val tab: AdminTab = AdminTab.USERS,
    val loading: Boolean = true,
    val users: List<AdminUser> = emptyList(),
    val usersError: String? = null,
    val auditEnabled: Boolean = false,
    val audit: List<AuditEntry> = emptyList(),
    val auditError: String? = null,
    val auditLoaded: Boolean = false,
)

@HiltViewModel
class AdminViewModel @Inject constructor(
    private val repo: AdminRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(AdminUi())
    val ui: StateFlow<AdminUi> = _ui.asStateFlow()

    init { loadUsers() }

    fun selectTab(t: AdminTab) {
        _ui.value = _ui.value.copy(tab = t)
        if (t == AdminTab.AUDIT && !_ui.value.auditLoaded) loadAudit()
        if (t == AdminTab.USERS && _ui.value.users.isEmpty()) loadUsers()
    }

    fun refresh() {
        if (_ui.value.tab == AdminTab.USERS) loadUsers() else loadAudit()
    }

    private fun loadUsers() {
        _ui.value = _ui.value.copy(loading = true, usersError = null)
        viewModelScope.launch {
            val (users, err) = repo.listUsers()
            _ui.value = _ui.value.copy(loading = false, users = users, usersError = if (users.isEmpty()) err else null)
        }
    }

    private fun loadAudit() {
        _ui.value = _ui.value.copy(loading = true, auditError = null)
        viewModelScope.launch {
            val r = repo.auditTools()
            _ui.value = _ui.value.copy(
                loading = false,
                auditEnabled = r.enabled,
                audit = r.entries,
                auditError = if (r.entries.isEmpty()) r.error else null,
                auditLoaded = true,
            )
        }
    }
}
