package com.hashmm.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.remote.RemoteConfigRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 主框架 ViewModel：登录后自动从 Supabase 同步后端地址（零配置）。 */
@HiltViewModel
class MainViewModel @Inject constructor(
    private val auth: AuthRepository,
    private val remoteConfig: RemoteConfigRepository,
) : ViewModel() {
    init {
        viewModelScope.launch {
            auth.isLoggedIn.distinctUntilChanged().collect { loggedIn ->
                if (loggedIn) remoteConfig.syncBackendUrl()
            }
        }
    }

    /** 当前登录邮箱（用于顶栏展示）。 */
    fun email(): String? = auth.currentEmail()

    /** 登录态流：未登录时工作台/动态等私人页面以"登录后查看"占位。 */
    val isLoggedIn = auth.isLoggedIn
}
