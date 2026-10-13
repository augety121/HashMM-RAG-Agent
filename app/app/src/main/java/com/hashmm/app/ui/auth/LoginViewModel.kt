package com.hashmm.app.ui.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.auth.NeedsEmailConfirmation
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class LoginUiState(
    val loading: Boolean = false,
    val error: String? = null,
    val info: String? = null,
    val success: Boolean = false,
    val awaitingCode: Boolean = false,   // 注册后等待输入 6 位邮箱验证码
    val pendingEmail: String = "",
)

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val auth: AuthRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(LoginUiState())
    val ui: StateFlow<LoginUiState> = _ui.asStateFlow()

    private var pendingEmail = ""
    private var pendingPassword = ""

    fun signIn(email: String, password: String) {
        if (email.isBlank() || password.isBlank()) {
            _ui.value = LoginUiState(error = "请输入邮箱和密码"); return
        }
        viewModelScope.launch {
            _ui.value = LoginUiState(loading = true)
            try {
                auth.signIn(email, password)
                _ui.value = LoginUiState(success = true)
            } catch (e: Exception) {
                _ui.value = LoginUiState(error = e.message ?: "登录失败，请检查邮箱密码")
            }
        }
    }

    fun signUp(email: String, password: String) {
        if (email.isBlank() || password.isBlank()) {
            _ui.value = LoginUiState(error = "请输入邮箱和密码"); return
        }
        if (!email.contains("@")) {
            _ui.value = LoginUiState(error = "邮箱格式不正确"); return
        }
        if (password.length < 6) {
            _ui.value = LoginUiState(error = "密码至少 6 位"); return
        }
        pendingEmail = email.trim(); pendingPassword = password
        viewModelScope.launch {
            _ui.value = LoginUiState(loading = true)
            try {
                auth.signUp(email, password)
                _ui.value = LoginUiState(success = true) // 项目关了邮箱验证 → 直接登录
            } catch (e: NeedsEmailConfirmation) {
                // 需输入邮箱收到的 6 位验证码
                _ui.value = LoginUiState(awaitingCode = true, pendingEmail = pendingEmail, info = e.message)
            } catch (e: Exception) {
                _ui.value = LoginUiState(error = e.message ?: "注册失败，请重试")
            }
        }
    }

    /** 提交邮箱收到的 6 位验证码完成注册。 */
    fun verifyCode(code: String) {
        if (code.trim().length < 6) { _ui.value = _ui.value.copy(error = "请输入 6 位验证码"); return }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null, info = null)
            try {
                auth.verifySignupOtp(pendingEmail, code)
                _ui.value = LoginUiState(success = true)
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(loading = false, error = e.message ?: "验证码错误或已过期")
            }
        }
    }

    /** 重新发送验证码（对未确认用户重新触发注册即重发）。 */
    fun resendCode() {
        if (pendingEmail.isBlank()) return
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null, info = null)
            try {
                auth.signUp(pendingEmail, pendingPassword)
                _ui.value = _ui.value.copy(loading = false, awaitingCode = true, pendingEmail = pendingEmail, info = "验证码已重新发送")
            } catch (e: NeedsEmailConfirmation) {
                _ui.value = _ui.value.copy(loading = false, awaitingCode = true, pendingEmail = pendingEmail, info = "验证码已重新发送到 $pendingEmail")
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(loading = false, error = e.message ?: "重新发送失败，请稍后再试")
            }
        }
    }

    /** 返回邮箱/密码表单（放弃当前验证码流程）。 */
    fun backToForm() { _ui.value = LoginUiState() }

    fun clearMessages() {
        _ui.value = _ui.value.copy(error = null, info = null)
    }
}
