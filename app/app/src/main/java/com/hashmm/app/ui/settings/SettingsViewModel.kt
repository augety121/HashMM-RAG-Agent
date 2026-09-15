package com.hashmm.app.ui.settings

import android.graphics.BitmapFactory
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.remote.AvatarRepository
import com.hashmm.app.data.settings.SettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
    private val avatarRepo: AvatarRepository,
) : ViewModel() {
    val clientUrl: StateFlow<String> =
        settings.clientUrl.stateIn(viewModelScope, SharingStarted.Eagerly, "")
    val isAutoConfigured: StateFlow<Boolean> =
        settings.isAutoConfigured.stateIn(viewModelScope, SharingStarted.Eagerly, false)
    val isLoggedIn: StateFlow<Boolean> =
        auth.isLoggedIn.stateIn(viewModelScope, SharingStarted.Eagerly, false)

    fun email(): String? = auth.currentEmail()
    fun uid(): String? = auth.currentUserId()

    // ── 头像（经后端中转，登录后自动拉取）──
    private val _avatar = MutableStateFlow<ImageBitmap?>(null)
    val avatar: StateFlow<ImageBitmap?> = _avatar.asStateFlow()
    private val _displayName = MutableStateFlow("")
    val displayName: StateFlow<String> = _displayName.asStateFlow()

    private var identityOwner: String? = null

    init {
        viewModelScope.launch {
            auth.isLoggedIn.collect { loggedIn ->
                val owner = auth.currentUserId()
                if (!loggedIn || owner == null) {
                    identityOwner = null
                    _avatar.value = null
                    _displayName.value = ""
                } else if (owner != identityOwner) {
                    identityOwner = owner
                    _avatar.value = null
                    _displayName.value = ""
                    refreshAvatar()
                    refreshDisplayName()
                }
            }
        }
    }

    fun refreshAvatar() {
        val id = auth.currentUserId() ?: return
        viewModelScope.launch {
            val bytes = avatarRepo.load(id) ?: return@launch
            val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return@launch
            _avatar.value = bmp.asImageBitmap()
        }
    }

    /** 上传头像（JPEG 字节），成功后自动刷新显示。 */
    fun uploadAvatar(bytes: ByteArray, onResult: (Boolean) -> Unit) {
        // 关键：先本地解码立即显示（不等后端往返），避免"上传了还是默认头像"——
        // 即使后端没部署头像接口，用户也能马上看到自己选的头像。
        try {
            val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            if (bmp != null) _avatar.value = bmp.asImageBitmap()
        } catch (_: Exception) {}
        viewModelScope.launch {
            val ok = avatarRepo.upload(bytes)
            if (ok) refreshAvatar()
            onResult(ok)
        }
    }

    // ── 昵称（profiles.display_name，与桌面端个人信息互通）──
    fun refreshDisplayName() {
        viewModelScope.launch {
            avatarRepo.loadDisplayName()?.let { _displayName.value = it }
        }
    }

    fun saveDisplayName(name: String, onResult: (Boolean) -> Unit) {
        val n = name.trim()
        if (n.isBlank()) { onResult(false); return }
        viewModelScope.launch {
            val ok = avatarRepo.saveDisplayName(n)
            if (ok) _displayName.value = n
            onResult(ok)
        }
    }
    fun saveUrl(url: String) { viewModelScope.launch { settings.setClientUrl(url) } }
    fun signOut() { viewModelScope.launch { auth.signOut() } }
}
