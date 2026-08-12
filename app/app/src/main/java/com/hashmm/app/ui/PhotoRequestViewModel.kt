package com.hashmm.app.ui

import android.content.ContentResolver
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.remote.PhoneRequest
import com.hashmm.app.data.remote.PhotoRequestRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * 在 App 前台且已登录时，轮询"电脑端向手机要照片"的请求；发现后通过 [pending] 提示 UI 弹出隐私确认。
 * 用户同意才会读取相册并发送，拒绝则回写 denied。隐私优先：绝不自动发照片。
 */
@HiltViewModel
class PhotoRequestViewModel @Inject constructor(
    private val repo: PhotoRequestRepository,
    private val auth: AuthRepository,
) : ViewModel() {

    private val _pending = MutableStateFlow<PhoneRequest?>(null)
    val pending: StateFlow<PhoneRequest?> = _pending.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private val _sent = MutableStateFlow(0)   // 自增，用于 UI 提示"已发送"
    val sent: StateFlow<Int> = _sent.asStateFlow()

    private val handled = HashSet<String>()   // 本次进程已处理过的请求 id，避免重复弹

    init {
        viewModelScope.launch {
            auth.isLoggedIn.distinctUntilChanged().collectLatest { loggedIn ->
                if (loggedIn) pollLoop()
            }
        }
    }

    private suspend fun pollLoop() {
        var idleDelayMs = 5_000L
        while (true) {
            try {
                if (_pending.value == null && !_busy.value) {
                    val reqs = repo.pollPending()
                    val next = reqs.firstOrNull { it.id !in handled }
                    if (next != null) {
                        _pending.value = next
                        idleDelayMs = 5_000L
                    } else {
                        // The endpoint may query remote persistence.  Fixed
                        // five-second polling produced 9-10 expensive reads a
                        // minute while the App was idle.  Back off to 30s and
                        // reset immediately when a request is observed.
                        idleDelayMs = (idleDelayMs * 2).coerceAtMost(30_000L)
                    }
                }
            } catch (ce: CancellationException) {
                throw ce
            } catch (_: Exception) { /* 轮询失败静默，下一轮再试 */ }
            delay(if (_pending.value != null || _busy.value) 5_000L else idleDelayMs)
        }
    }

    /** 用户拒绝：回写 denied，不读取相册。 */
    fun deny() {
        val r = _pending.value ?: return
        handled.add(r.id)
        _pending.value = null
        viewModelScope.launch { repo.setStatus(r.id, "denied") }
    }

    /** 用户同意（且已授予相册权限）：读取最近一张照片并发送。 */
    fun approve(cr: ContentResolver) {
        val r = _pending.value ?: return
        handled.add(r.id)
        _pending.value = null
        viewModelScope.launch {
            _busy.value = true
            try {
                val ok = repo.fulfill(r, cr)
                if (ok) _sent.value = _sent.value + 1
            } finally {
                _busy.value = false
            }
        }
    }
}
