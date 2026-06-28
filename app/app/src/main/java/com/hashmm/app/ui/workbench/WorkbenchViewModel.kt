package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.settings.SettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * HashMM 工作台 ViewModel：提供客户端地址 + 当前 Supabase access token。
 *
 * WebView 用同一个 Supabase 会话的 access token 登录 HashMM 客户端（后端已支持验 Supabase token），
 * 实现 App 与桌面客户端的统一身份联动。
 */
@HiltViewModel
class WorkbenchViewModel @Inject constructor(
    private val settings: SettingsStore,
    private val supabase: SupabaseClient
) : ViewModel() {

    val clientUrl: StateFlow<String> =
        settings.clientUrl.stateIn(viewModelScope, SharingStarted.Eagerly, "")

    /** 当前 Supabase 会话的 access token，注入到 WebView 让客户端自动登录。 */
    fun accessToken(): String? = supabase.auth.currentSessionOrNull()?.accessToken
    fun refreshToken(): String? = supabase.auth.currentSessionOrNull()?.refreshToken

    fun saveUrl(url: String) {
        viewModelScope.launch { settings.setClientUrl(url) }
    }
}
