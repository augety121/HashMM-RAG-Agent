package com.hashmm.app.data.remote

import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.auth
import io.github.jan.supabase.postgrest.postgrest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import org.json.JSONObject
import java.time.Instant
import javax.inject.Inject
import javax.inject.Singleton

data class RemoteHost(val id: String, val name: String, val platform: String)

/**
 * 接力（Codex 式设备交接）：把一个对话交接给同账号在线的桌面主机继续。
 * 在线主机来自 remote_presence；交接信号写入 remote_signals（kind=handoff，payload={conv_id,title}）。
 * 对话本身在后端持续运行，接力只是把「活跃视图」转交给桌面端。
 */
@Singleton
class RelayRepository @Inject constructor(
    private val supabase: SupabaseClient,
) {
    @Serializable
    private data class PresenceRow(val host_id: String, val name: String? = null, val platform: String? = null, val updated_at: String? = null)

    @Serializable
    private data class HandoffInsert(val room: String, val recipient: String, val sender: String, val kind: String, val payload: JsonElement? = null)

    private fun uid(): String? = supabase.auth.currentUserOrNull()?.id

    /** 同账号在线桌面主机（45s 内有心跳）。 */
    suspend fun onlineHosts(): List<RemoteHost> = withContext(Dispatchers.IO) {
        val room = uid() ?: return@withContext emptyList()
        try {
            val rows = supabase.postgrest.from("remote_presence").select {
                filter { eq("room", room) }
            }.decodeList<PresenceRow>()
            val now = System.currentTimeMillis()
            rows.filter { r ->
                val t = r.updated_at?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() }
                t == null || now - t < 45_000
            }
                .distinctBy { (it.name ?: "").ifBlank { it.host_id } }
                .map { RemoteHost(it.host_id, it.name ?: "桌面端", it.platform ?: "") }
        } catch (e: Exception) {
            emptyList()
        }
    }

    /** 交接一个对话到指定桌面主机。 */
    suspend fun handoff(convId: String, title: String, hostId: String): Boolean = withContext(Dispatchers.IO) {
        val room = uid() ?: return@withContext false
        try {
            val payload = Json.parseToJsonElement(JSONObject().put("conv_id", convId).put("title", title).put("ts", System.currentTimeMillis()).toString())
            supabase.postgrest.from("remote_signals").insert(
                HandoffInsert(room = room, recipient = "host:$hostId", sender = "relay:$room", kind = "handoff", payload = payload)
            )
            true
        } catch (e: Exception) {
            false
        }
    }
}
