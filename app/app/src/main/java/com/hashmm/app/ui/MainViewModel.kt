package com.hashmm.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.remote.RemoteConfigRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.delay
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 主框架 ViewModel：登录后自动从 Supabase 同步后端地址（零配置）。 */
@HiltViewModel
class MainViewModel @Inject constructor(
    private val auth: AuthRepository,
    private val remoteConfig: RemoteConfigRepository,
) : ViewModel() {
    private var configRefreshJob: Job? = null

    init {
        viewModelScope.launch {
            auth.isLoggedIn.distinctUntilChanged().collect { loggedIn ->
                configRefreshJob?.cancel()
                configRefreshJob = if (loggedIn) launch { keepRemoteConfigFresh() } else null
            }
        }
    }

    private suspend fun keepRemoteConfigFresh() {
        val retry = longArrayOf(0L, 2_000L, 5_000L, 15_000L, 60_000L, 300_000L, 900_000L)
        var attempt = 0
        while (true) {
            if (attempt > 0) delay(retry[attempt.coerceAtMost(retry.lastIndex)])
            when (remoteConfig.syncBackendUrl(force = true)) {
                is com.hashmm.app.data.remote.RemoteConfigSyncResult.Success,
                com.hashmm.app.data.remote.RemoteConfigSyncResult.Skipped -> {
                    attempt = 0
                    delay(6 * 60 * 60 * 1000L)
                }
                is com.hashmm.app.data.remote.RemoteConfigSyncResult.Failure -> {
                    attempt = (attempt + 1).coerceAtMost(retry.lastIndex)
                }
            }
        }
    }

    /** 登录、网络恢复或用户点击重试时可立即触发；仓库内部 single-flight。 */
    fun refreshConnection() {
        configRefreshJob?.cancel()
        configRefreshJob = viewModelScope.launch { keepRemoteConfigFresh() }
    }

    /** 当前登录邮箱（用于顶栏展示）。 */
    fun email(): String? = auth.currentEmail()
    fun isAdmin(): Boolean = auth.isAdmin()

    /** 登录态流：未登录时工作台/动态等私人页面以"登录后查看"占位。 */
    val isLoggedIn = auth.isLoggedIn
}
