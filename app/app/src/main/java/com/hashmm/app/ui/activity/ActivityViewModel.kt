package com.hashmm.app.ui.activity

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ActiveChat
import com.hashmm.app.data.remote.Activity
import com.hashmm.app.data.remote.ActivityRealtimeRepository
import com.hashmm.app.data.remote.JobInfo
import com.hashmm.app.data.remote.UsageRepository
import com.hashmm.app.data.remote.UsageStat
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class ActivityViewModel @Inject constructor(
    private val repo: ActivityRealtimeRepository,
    private val usageRepo: UsageRepository,
) : ViewModel() {
    private val _activity = MutableStateFlow(Activity())
    val activity: StateFlow<Activity> = _activity.asStateFlow()

    private val _loading = MutableStateFlow(true)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    // 概览（近 7 天用量）；为空表示未取到/未连后端，UI 则不显示该卡。
    private val _today = MutableStateFlow<UsageStat?>(null)
    val today: StateFlow<UsageStat?> = _today.asStateFlow()

    init {
        // 初次拉取当前进行中的任务
        viewModelScope.launch {
            refresh()
            _loading.value = false
        }
        // 近 7 天用量概览
        viewModelScope.launch {
            try {
                val u = usageRepo.usage(7)
                if (u.error == null && (u.requests > 0 || u.tokens > 0L)) _today.value = u
            } catch (e: Exception) { /* 忽略：不显示概览卡 */ }
        }
        // 实时订阅：client_activity 任一变更 → 重拉（真实时推送，非轮询）
        viewModelScope.launch {
            try {
                repo.subscribeActivity { viewModelScope.launch { refresh() } }
            } catch (e: Exception) {
                // 实时通道不可用时，保留已拉取内容，不致命
            }
        }
    }

    private suspend fun refresh() {
        try {
            val rows = repo.fetchActivity()
            val chats = rows.filter { it.kind == "chat" }
                .map { ActiveChat(convId = it.id, title = it.title.ifBlank { "对话" }) }
            val jobs = rows.filter { it.kind == "job" }
                .map { JobInfo(id = it.id, kind = it.kind, status = it.status, done = it.done, total = it.total, message = it.title) }
            _activity.value = Activity(activeChats = chats, jobs = jobs)
        } catch (e: Exception) {
            // 保留旧值
        }
    }
}
