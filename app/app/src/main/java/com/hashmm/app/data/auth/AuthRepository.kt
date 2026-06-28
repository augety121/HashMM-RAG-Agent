package com.hashmm.app.data.auth

import com.hashmm.app.data.cache.LocalStore
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.auth
import io.github.jan.supabase.auth.OtpType
import io.github.jan.supabase.auth.providers.builtin.Email
import io.github.jan.supabase.auth.status.SessionStatus
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

/** 注册成功但需用户去邮箱点验证链接才能登录。 */
class NeedsEmailConfirmation(message: String) : Exception(message)

/** 仅做登录鉴权（与桌面客户端共用 Supabase 账号）。退出账号时清空本地缓存，保证账号隔离。 */
@Singleton
class AuthRepository @Inject constructor(
    private val supabase: SupabaseClient,
    private val localStore: LocalStore,
) {

    /**
     * 登录态流：会话加载完成后发出 true/false。
     * supabase-kt 冷启动先发 Initializing（从存储异步加载），过滤掉避免登录态抖动。
     */
    val isLoggedIn: Flow<Boolean> = supabase.auth.sessionStatus
        .filter { it !is SessionStatus.Initializing }
        .map { it is SessionStatus.Authenticated }

    fun currentEmail(): String? = supabase.auth.currentUserOrNull()?.email

    /** 当前登录用户的 Supabase uuid（本地缓存按它分目录）；未登录为 null。 */
    fun currentUserId(): String? = supabase.auth.currentUserOrNull()?.id

    /** 当前 access token（注入 WebView / 远程信令鉴权用）；未登录为 null。 */
    fun currentToken(): String? = supabase.auth.currentSessionOrNull()?.accessToken

    suspend fun signIn(email: String, password: String) {
        supabase.auth.signInWith(Email) {
            this.email = email.trim()
            this.password = password
        }
    }

    /** 注册新账号（与桌面客户端共用同一 Supabase 用户体系）。若开启邮箱验证则抛 NeedsEmailConfirmation。 */
    suspend fun signUp(email: String, password: String) {
        supabase.auth.signUpWith(Email) {
            this.email = email.trim()
            this.password = password
        }
        // 开启邮箱验证时：注册后没有 session，需要用户输入邮箱收到的 6 位验证码确认
        if (supabase.auth.currentUserOrNull() == null) {
            throw NeedsEmailConfirmation("验证码已发送到 ${email.trim()}，请查收邮件并在下方输入 6 位验证码完成注册")
        }
    }

    /** 校验邮箱收到的 6 位注册验证码；成功后直接建立会话（=登录）。 */
    suspend fun verifySignupOtp(email: String, code: String) {
        supabase.auth.verifyEmailOtp(
            type = OtpType.Email.SIGNUP,
            email = email.trim(),
            token = code.trim(),
        )
    }

    suspend fun signOut() {
        val uid = currentUserId()
        supabase.auth.signOut()
        if (uid != null) localStore.clear(uid)  // 清空该账号本地缓存
    }
}
