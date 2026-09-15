package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.TimeZone
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class ScheduledTask(
    val id: String,
    val name: String,
    val action: String,
    val enabled: Boolean,
    val scheduleKind: String,
    val intervalSeconds: Int,
    val dailyAt: String,
    val timezone: String,
    val resultDestination: String,
    val lastRunSec: Long,
    val nextRunSec: Long,
    val lastStatus: String,
    val lastResult: String,
)

data class ScheduledData(
    val schedulerEnabled: Boolean = false,
    val actions: List<String> = emptyList(),
    val tasks: List<ScheduledTask> = emptyList(),
    val error: String? = null,
)

@Singleton
class ScheduledRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = SharedHttp.base.newBuilder().callTimeout(20, TimeUnit.SECONDS).build()
    private val jsonType = "application/json".toMediaType()

    private suspend fun base(): String {
        val raw = settings.clientUrl.first().trim().trimEnd('/')
        if (raw.isBlank()) return ""
        val schemeless = raw.removePrefix("https://").removePrefix("http://")
        val host = schemeless.substringBefore('/').substringBefore(':')
        val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(host)
        return when { isIp -> "http://$schemeless"; raw.startsWith("http") -> raw; else -> "https://$raw" }
    }

    private fun sec(value: Double): Long = when {
        value <= 0 -> 0
        value > 1e12 -> (value / 1000).toLong()
        else -> value.toLong()
    }

    private fun error(code: Int, body: String?): String {
        val detail = runCatching { JSONObject(body ?: "{}").optString("detail") }.getOrNull().orEmpty()
        return when (code) {
            401 -> "登录状态已失效，请重新登录"
            403 -> "当前账号无权操作这项例行任务"
            404 -> "例行任务不存在，或当前服务端还未更新"
            else -> detail.ifBlank { "操作失败（$code）" }
        }
    }

    suspend fun list(): ScheduledData = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext ScheduledData(error = "未连接后端或未登录")
        try {
            val request = Request.Builder().url("$base/api/user-work/routines")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(request).execute().use { response ->
                val text = response.body?.string()
                if (!response.isSuccessful) return@use ScheduledData(error = error(response.code, text))
                val root = JSONObject(text ?: "{}")
                val actions = mutableListOf<String>()
                root.optJSONArray("actions")?.let { arr ->
                    for (i in 0 until arr.length()) {
                        val value = when (val raw = arr.opt(i)) {
                            is JSONObject -> raw.optString("id")
                            is String -> raw
                            else -> ""
                        }
                        value.takeIf { it.isNotBlank() }?.let(actions::add)
                    }
                }
                val tasks = mutableListOf<ScheduledTask>()
                root.optJSONArray("items")?.let { arr ->
                    for (i in 0 until arr.length()) {
                        val item = arr.optJSONObject(i) ?: continue
                        val enabledValue = item.opt("enabled")
                        val taskEnabled = when (enabledValue) {
                            is Boolean -> enabledValue
                            is Number -> enabledValue.toInt() != 0
                            is String -> enabledValue.equals("true", true) || enabledValue == "1"
                            else -> true
                        }
                        tasks.add(ScheduledTask(
                            id = item.optString("id"),
                            name = item.optString("name", item.optString("action", "任务")),
                            action = item.optString("action"),
                            enabled = taskEnabled,
                            scheduleKind = item.optString("schedule_kind", "interval"),
                            intervalSeconds = item.optInt("interval_seconds", 3600),
                            dailyAt = item.optString("daily_at"),
                            timezone = item.optString("timezone", "UTC"),
                            resultDestination = item.optString("result_destination", "work_ledger"),
                            lastRunSec = sec(item.optDouble("last_run", 0.0)),
                            nextRunSec = sec(item.optDouble("next_run", 0.0)),
                            lastStatus = item.optString("last_status"),
                            lastResult = item.optString("last_result"),
                        ))
                    }
                }
                ScheduledData(root.optBoolean("available", false), actions, tasks)
            }
        } catch (_: Exception) { ScheduledData(error = "网络错误") }
    }

    suspend fun create(action: String, name: String, intervalHours: Int, convId: String): String? {
        val body = JSONObject()
            .put("action", action)
            .put("name", name.ifBlank { action })
            .put("schedule_kind", "interval")
            .put("interval_seconds", intervalHours.coerceIn(1, 720) * 3600)
            .put("timezone", TimeZone.getDefault().id)
            .put("result_destination", if (convId.isBlank()) "work_ledger" else "conversation")
            .apply { if (convId.isNotBlank()) put("conversation_id", convId) }
        return mutate("/api/user-work/routines", "POST", body)
    }

    suspend fun runNow(id: String): String? = mutate("/api/user-work/routines/$id/run", "POST", JSONObject())
    suspend fun toggle(id: String, enabled: Boolean): String? =
        mutate("/api/user-work/routines/$id/toggle", "POST", JSONObject().put("enabled", enabled))
    suspend fun delete(id: String): String? = mutate("/api/user-work/routines/$id", "DELETE", null)

    private suspend fun mutate(path: String, method: String, body: JSONObject?): String? = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext "未连接后端或未登录"
        try {
            val builder = Request.Builder().url("$base$path").header("Authorization", "Bearer $token")
            when (method) {
                "DELETE" -> builder.delete()
                else -> builder.method(method, (body ?: JSONObject()).toString().toRequestBody(jsonType))
            }
            http.newCall(builder.build()).execute().use { response ->
                val text = response.body?.string()
                if (response.isSuccessful) null else error(response.code, text)
            }
        } catch (_: Exception) { "网络错误" }
    }
}
