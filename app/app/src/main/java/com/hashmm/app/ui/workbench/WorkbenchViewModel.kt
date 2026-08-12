package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.SharedHttp
import com.hashmm.app.data.settings.SettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URLEncoder
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

    suspend fun webViewEntryUrl(targetUrl: String): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val session = supabase.auth.currentSessionOrNull() ?: error("请先登录 HashMM")
            val access = session.accessToken
            require(access.isNotBlank()) { "登录会话不可用" }
            val origin = WebViewSecurity.originOf(targetUrl) ?: error("客户端地址无效")
            val payload = JSONObject()
                .put("refresh_token", session.refreshToken.orEmpty())
                .toString()
                .toRequestBody("application/json; charset=utf-8".toMediaType())
            val request = Request.Builder()
                .url("$origin/api/auth/webview/code")
                .header("Authorization", "Bearer $access")
                .header("Cache-Control", "no-store")
                .post(payload)
                .build()
            SharedHttp.base.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) error("工作台身份联动失败（${response.code}）")
                val code = JSONObject(body).optString("code")
                if (code.isBlank()) error("工作台身份联动返回无效")
                val separator = if (targetUrl.contains("?")) "&" else "?"
                targetUrl + separator + "wv_code=" + URLEncoder.encode(code, "UTF-8")
            }
        }
    }

    fun saveUrl(url: String) {
        viewModelScope.launch { settings.setClientUrl(url) }
    }
}
