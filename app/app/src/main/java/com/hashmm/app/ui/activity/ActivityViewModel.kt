package com.hashmm.app.ui.activity

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.MobileWorkSnapshot
import com.hashmm.app.data.remote.ScheduledData
import com.hashmm.app.data.remote.ScheduledRepository
import com.hashmm.app.data.remote.UsageStat
import com.hashmm.app.data.remote.WorkRuntimeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * “今天”只聚合用户今天真正需要知道的两类事实：
 * 1. 同账号工作运行记录与待处理事项；
 * 2. 下一次自动任务安排。
 *
 * 它不再并行请求旧动态流、用量、能力诊断等互相重叠的数据源。这样页面与桌面端
 * 共用一份工作事实，网络抖动时也不会被多个接口的局部失败清空。
 */
data class TodayUi(
    val work: MobileWorkSnapshot = MobileWorkSnapshot(),
    val schedules: ScheduledData = ScheduledData(),
    val loading: Boolean = true,
    val refreshing: Boolean = false,
)

internal data class UsageRefreshDecision(val snapshot: UsageStat?, val error: String?)

// 保留为历史回归测试使用：失败的刷新不能把最近一次可信用量覆盖成 0。
internal fun reconcileUsage(previous: UsageStat?, fetched: UsageStat): UsageRefreshDecision =
    if (fetched.error == null) UsageRefreshDecision(fetched, null)
    else UsageRefreshDecision(previous, fetched.error)

@HiltViewModel
class ActivityViewModel @Inject constructor(
    private val workRuntimeRepository: WorkRuntimeRepository,
    private val scheduledRepository: ScheduledRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(TodayUi())
    val ui: StateFlow<TodayUi> = _ui.asStateFlow()
    private var refreshJob: Job? = null

    init {
        refresh(force = false)
        viewModelScope.launch {
            while (isActive) {
                workRuntimeRepository.streamChanges { feed ->
                    val current = _ui.value.work
                    val merged = current.runs.associateBy { it.id }.toMutableMap()
                    feed.items.forEach { merged[it.id] = it }
                    _ui.value = _ui.value.copy(
                        work = current.copy(
                            cursor = feed.nextCursor,
                            highWaterCursor = feed.highWaterCursor,
                            runs = merged.values.sortedByDescending { it.updatedAt },
                            actionInbox = feed.actionInbox,
                            notice = null,
                            error = null,
                        ),
                    )
                }
                delay(3_000L)
            }
        }
        viewModelScope.launch {
            while (isActive) {
                delay(60_000L)
                refresh(force = false)
            }
        }
    }

    fun refreshAll() = refresh(force = true)

    private fun refresh(force: Boolean) {
        if (!force && refreshJob?.isActive == true) return
        if (force) refreshJob?.cancel()
        _ui.value = _ui.value.copy(
            loading = _ui.value.work.runs.isEmpty(),
            refreshing = force,
        )
        refreshJob = viewModelScope.launch {
            val (work, schedules) = coroutineScope {
                val workCall = async { workRuntimeRepository.fetch(force) }
                val scheduleCall = async { scheduledRepository.list() }
                workCall.await() to scheduleCall.await()
            }
            _ui.value = TodayUi(
                work = work,
                schedules = schedules,
                loading = false,
                refreshing = false,
            )
        }
    }
}
