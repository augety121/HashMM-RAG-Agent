package com.hashmm.app.data.remote

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/**
 * V269 后台聊天直播管理器（对齐桌面端 store.liveStreams，直接回应"切走再回来聊天还在继续、
 * 后台执行、重进可见"）。
 *
 * 病根：以前聊天流式跑在 `viewModelScope` 里——离开会话页 ViewModel 被清、协程随之取消，SSE 连接
 * 关闭、后端因客户端断连而中止；回来是全新 ViewModel、本地 streaming 占位消息已丢，于是"点进去先
 * 空白、过会儿才出现"。
 *
 * 解法：把真正的流式请求放到**应用级作用域**（不随任何页面销毁而取消），实时内容按 convId 挂在
 * 这个单例的 StateFlow 上。ChatDetailViewModel 观察它：
 *   · 自己发起 → 调 stream(convId, msg, onContent)，内容边到边更新气泡；
 *   · 切走再回来（新 ViewModel）→ 观察 streams 里该会话仍在跑的直播，立刻显示当前进度，不再空白。
 * 与后端 V295 的"partial 每 1.5s 落库"配合，整页杀进程重开也能靠拉取续看。
 *
 * 线程安全：状态用 MutableStateFlow + ConcurrentHashMap 记在跑的任务；同会话不重复起流。
 */
@Singleton
class LiveChatManager @Inject constructor(
    private val liveRepo: ChatLiveRepository,
) {
    /** 一条会话的后台直播快照。streaming=false 且 content 有值 ⇒ 刚结束（观察者据此收尾拉规范列表）。 */
    data class LiveChat(
        val convId: String,
        val streaming: Boolean,
        val content: String,
        val query: String,
        val startedAt: Long,
    )

    // 应用级作用域：SupervisorJob 保证单条流失败不影响其它；IO 调度跑网络。永不随页面取消。
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val jobs = ConcurrentHashMap<String, Deferred<Boolean>>()

    private val _streams = MutableStateFlow<Map<String, LiveChat>>(emptyMap())
    val streams: StateFlow<Map<String, LiveChat>> = _streams.asStateFlow()

    fun liveFor(convId: String): LiveChat? = _streams.value[convId]
    fun isStreaming(convId: String): Boolean = _streams.value[convId]?.streaming == true

    private fun put(convId: String, v: LiveChat) {
        _streams.value = _streams.value.toMutableMap().apply { put(convId, v) }
    }

    private fun update(convId: String, transform: (LiveChat) -> LiveChat) {
        val cur = _streams.value[convId] ?: return
        put(convId, transform(cur))
    }

    private fun remove(convId: String) {
        _streams.value = _streams.value.toMutableMap().apply { remove(convId) }
        jobs.remove(convId)
    }

    /**
     * 在应用级作用域跑一次后端流式，不随调用方（ViewModel）被清而取消。
     *
     * onContent 回调接收**累计内容**（不是增量 token），便于调用方直接替换气泡文本。
     * 返回 true=已连上并由后端落库完成；false=连接失败（调用方走兜底非流式/直连）。
     *
     * 关键点：调用方在 viewModelScope 里 await 本函数——若 ViewModel 被清，await 被取消（抛
     * CancellationException），但底层 Deferred 属于 appScope、**不会被取消**，流照常在后台跑完并更新
     * streams；用户重开时新 ViewModel 观察 streams 就能续看。
     */
    suspend fun stream(convId: String, message: String, onContent: (String) -> Unit): Boolean {
        // 同会话已在跑：直接 await 现有任务（避免重复发送），期间把最新内容回吐给调用方
        jobs[convId]?.let { existing ->
            liveFor(convId)?.let { onContent(it.content) }
            return runCatching { existing.await() }.getOrDefault(false)
        }
        put(convId, LiveChat(convId, streaming = true, content = "", query = message,
            startedAt = System.currentTimeMillis()))
        val deferred: Deferred<Boolean> = appScope.async {
            val acc = StringBuilder()
            val ok = liveRepo.streamMessage(convId, message) { token ->
                acc.append(token)
                update(convId) { it.copy(content = acc.toString()) }
            }
            // 标记结束（保留内容，供正在观察的 ViewModel 收尾读取），随后移除
            update(convId) { it.copy(streaming = false) }
            ok
        }
        jobs[convId] = deferred
        // 边跑边把累计内容回吐给调用方（观察 streams 也可，但回调让原发起端零改动地续用旧逻辑）
        return try {
            // 转发进度：直到底层任务完成
            while (deferred.isActive) {
                liveFor(convId)?.let { onContent(it.content) }
                kotlinx.coroutines.delay(120)
            }
            val ok = runCatching { deferred.await() }.getOrDefault(false)
            liveFor(convId)?.let { onContent(it.content) }
            remove(convId)
            ok
        } catch (ce: kotlinx.coroutines.CancellationException) {
            // 调用方（ViewModel）被清：底层 deferred 属于 appScope，继续在后台跑完；这里只是不再转发。
            // 后台完成时会 update(streaming=false)；为避免泄漏，起一个 appScope 收尾把它移除。
            appScope.launch {
                runCatching { deferred.await() }
                remove(convId)
            }
            throw ce
        }
    }
}
