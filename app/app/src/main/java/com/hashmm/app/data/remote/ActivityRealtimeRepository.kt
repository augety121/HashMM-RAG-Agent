package com.hashmm.app.data.remote

import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.auth
import io.github.jan.supabase.postgrest.postgrest
import io.github.jan.supabase.postgrest.query.Order
import io.github.jan.supabase.postgrest.query.filter.FilterOperator
import io.github.jan.supabase.realtime.PostgresAction
import io.github.jan.supabase.realtime.channel
import io.github.jan.supabase.realtime.postgresChangeFlow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/** 与 Supabase client_activity 表对应。 */
@Serializable
data class ClientActivity(
    val id: String,
    @SerialName("user_id") val userId: String = "",
    val kind: String = "chat",
    val title: String = "",
    val status: String = "active",
    val done: Int = 0,
    val total: Int = 0,
)

/**
 * 实时任务活动：用 Supabase Realtime 订阅 client_activity，替代轮询。
 *  · fetchActivity()      —— 拉当前进行中的任务（RLS 只返回本人）。
 *  · subscribeActivity()  —— 订阅本表变更，任一变更回调 onChange（上层据此重拉）。
 * 参考 HashLens 验证过的写法：channel 名唯一（避免复用已 join 的 channel 抛异常）、finally 里异步 unsubscribe。
 */
@Singleton
class ActivityRealtimeRepository @Inject constructor(
    private val supabase: SupabaseClient,
) {
    suspend fun fetchActivity(): List<ClientActivity> =
        supabase.postgrest.from("client_activity").select {
            order("updated_at", Order.DESCENDING)
        }.decodeList()

    /** 阻塞式订阅：会一直挂起收集变更，直到所在协程被取消。每次变更调用 onChange。 */
    suspend fun subscribeActivity(onChange: () -> Unit) {
        val uid = supabase.auth.currentUserOrNull()?.id ?: return
        val uniqueId = UUID.randomUUID().toString().take(8)
        val channel = supabase.channel("hashmm-activity-$uid-$uniqueId")
        try {
            val changes = channel.postgresChangeFlow<PostgresAction>(schema = "public") {
                table = "client_activity"
                filter(column = "user_id", operator = FilterOperator.EQ, value = uid)
            }
            channel.subscribe(blockUntilSubscribed = false)
            changes.collect { onChange() }
        } finally {
            // 协程被取消时 unsubscribe 不能在已取消的上下文里 suspend，故另起一次性协程清理
            CoroutineScope(Dispatchers.IO).launch { runCatching { channel.unsubscribe() } }
        }
    }
}
