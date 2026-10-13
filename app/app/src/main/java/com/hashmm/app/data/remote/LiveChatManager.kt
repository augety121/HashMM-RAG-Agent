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
import kotlinx.coroutines.flow.update
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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
    private val liveRepo: LiveStreamSource,   // V308：依赖接口而非具体类 → 单测可注入 Fake
) {
    /** 一条会话的后台直播快照。streaming=false 且 content 有值 ⇒ 刚结束（观察者据此收尾拉规范列表）。 */
    data class LiveChat(
        val convId: String,
        val streaming: Boolean,
        val content: String,
        val query: String,
        val startedAt: Long,
        val taskContract: String = "",
        val todo: String = "",
        val progress: String = "",
        val runManifest: String = "",
        val lastEvent: String = "",
        val stepCount: Int = 0,
        val turnId: String = "",
        val turnSteerable: Boolean = false,
        val turnStatus: String = "",
    )

    // 应用级作用域：SupervisorJob 保证单条流失败不影响其它；IO 调度跑网络。永不随页面取消。
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val jobs = ConcurrentHashMap<String, Deferred<Boolean>>()

    private val _streams = MutableStateFlow<Map<String, LiveChat>>(emptyMap())
    val streams: StateFlow<Map<String, LiveChat>> = _streams.asStateFlow()

    fun liveFor(convId: String): LiveChat? = _streams.value[convId]
    fun isStreaming(convId: String): Boolean = _streams.value[convId]?.streaming == true

    /** SSE metadata must be parseable in both Android and plain JVM tests. */
    private fun eventJson(data: String) = runCatching {
        Json.parseToJsonElement(data).jsonObject
    }.getOrNull()

    // V308 修 P1 状态竞态：以下三个方法原本都是对 _streams.value 的【普通读—改—写】
    //   （读出旧 Map → toMutableMap() → put/remove → 赋回 .value）。
    // 多个会话的流在 IO 线程上并发更新时，两次读-改-写会互相覆盖：A 读到 M0，B 也读到 M0，
    // A 写 M0+a，B 写 M0+b —— 结果只剩 b，a 的状态凭空丢失（表现为某个会话的流式内容
    // 突然不再更新 / 气泡卡住）。
    // 改用 MutableStateFlow.update { }：它内部是 compareAndSet 循环，读-改-写整体原子，
    // 并发下不会丢更新。
    private fun put(convId: String, v: LiveChat) {
        _streams.update { cur -> cur + (convId to v) }
    }

    private fun update(convId: String, transform: (LiveChat) -> LiveChat) {
        _streams.update { cur ->
            val old = cur[convId] ?: return@update cur   // 不存在则原样返回，不做无谓写入
            cur + (convId to transform(old))
        }
    }

    private fun remove(convId: String) {
        _streams.update { cur -> cur - convId }
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
        // V308 修 P1 并发竞态：原实现是「先 jobs[convId] 检查 → 再 put/async → 再 jobs[convId]=deferred」
        // 的【非原子】check-then-act。两个协程可能同时发现任务不存在，于是对同一会话发出【两次】
        // 后端请求（重复扣费、重复落库、两条流互相覆盖 streams）。
        // 改用 ConcurrentHashMap.computeIfAbsent —— 同一 key 的映射函数由 CHM 保证【只执行一次】，
        // 并发调用中只有一个会真正创建并启动流，其余直接拿到同一个 Deferred 去 await。
        var created = false
        val deferred: Deferred<Boolean> = jobs.computeIfAbsent(convId) { key ->
            created = true
            put(key, LiveChat(key, streaming = true, content = "", query = message,
                startedAt = System.currentTimeMillis()))
            appScope.async {
                val acc = StringBuilder()
                val ok = liveRepo.streamMessageWithEvents(
                    key,
                    message,
                    onToken = { token ->
                        acc.append(token)
                        update(key) { it.copy(content = acc.toString()) }
                    },
                    onEvent = { event ->
                        update(key) { live ->
                            when (event.type) {
                                "turn_started", "turn_state" -> {
                                    val turn = eventJson(event.data)
                                    live.copy(
                                        turnId = turn?.get("turn_id")?.jsonPrimitive?.contentOrNull.orEmpty(),
                                        turnSteerable = turn?.get("steerable")?.jsonPrimitive?.booleanOrNull ?: false,
                                        turnStatus = turn?.get("status")?.jsonPrimitive?.contentOrNull ?: "running",
                                        lastEvent = event.type,
                                    )
                                }
                                "steer_applied" -> live.copy(lastEvent = event.type)
                                "task_contract" -> live.copy(taskContract = event.data, lastEvent = event.type)
                                "todo" -> live.copy(todo = event.data, lastEvent = event.type)
                                "progress" -> live.copy(progress = event.data, lastEvent = event.type)
                                "step_start", "step_done" -> live.copy(
                                    lastEvent = event.type,
                                    stepCount = if (event.type == "step_start") live.stepCount + 1 else live.stepCount,
                                )
                                "done" -> {
                                    val done = eventJson(event.data)
                                    val manifest = runCatching {
                                        done?.get("run_manifest")?.jsonObject?.toString().orEmpty()
                                    }.getOrDefault("")
                                    live.copy(
                                        runManifest = manifest,
                                        lastEvent = event.type,
                                        turnSteerable = false,
                                        turnStatus = done?.get("status")?.jsonPrimitive?.contentOrNull ?: "complete",
                                    )
                                }
                                else -> live
                            }
                        }
                    },
                )
                // 标记结束（保留内容，供正在观察的 ViewModel 收尾读取），随后移除
                update(key) { it.copy(streaming = false) }
                ok
            }
        }

        // 复用了已有任务（本次没创建）：把当前累计内容先回吐一次，然后走下面统一的转发/await 逻辑。
        if (!created) {
            liveFor(convId)?.let { onContent(it.content) }
        }
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

    suspend fun activeTurn(convId: String): ActiveTurnState? {
        liveFor(convId)?.let { live ->
            if (live.turnId.isNotBlank()) {
                return ActiveTurnState(
                    turnId = live.turnId,
                    conversationId = convId,
                    steerable = live.turnSteerable,
                    status = live.turnStatus.ifBlank { "running" },
                    mode = "agent_loop",
                )
            }
        }
        return liveRepo.activeTurn(convId)
    }

    suspend fun steer(convId: String, content: String, clientMessageId: String): SteerTurnResult? {
        val turn = activeTurn(convId) ?: return null
        if (!turn.steerable) return null
        return liveRepo.steerTurn(convId, turn.turnId, content, clientMessageId)
    }

    suspend fun interrupt(convId: String): Boolean {
        val turn = activeTurn(convId) ?: return false
        val accepted = liveRepo.interruptTurn(convId, turn.turnId)
        if (accepted) {
            update(convId) { it.copy(turnSteerable = false, turnStatus = "interrupting") }
        }
        return accepted
    }

    /** Stop means stop: cancel both the app-level coroutine and the real HTTP transport. */
    fun cancel(convId: String) {
        jobs.remove(convId)?.cancel()
        liveRepo.cancelStream(convId)
        remove(convId)
    }
}
