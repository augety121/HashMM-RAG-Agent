package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 语音转文字：把录好的音频上传到电脑端后端 /api/stt（本地 Whisper，GPU，免费、不出网），返回识别文本。
 * 用于「设备没有系统语音识别服务」的兜底——保证任何手机都能语音输入。
 * 失败返回 null，绝不抛错。
 */
@Singleton
class SttRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = SharedHttp.base.newBuilder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .callTimeout(90, TimeUnit.SECONDS)
        .build()

    private suspend fun base(): String {
        val b = settings.clientUrl.first().trim().trimEnd('/')
        if (b.isBlank()) return ""
        val schemeless = b.removePrefix("https://").removePrefix("http://")
        val host = schemeless.substringBefore("/").substringBefore(":")
        val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(host)
        return when {
            isIp -> "http://$schemeless"
            b.startsWith("http") -> b
            else -> "https://$b"
        }
    }

    /** 上传音频返回识别文本；失败/空返回 null。 */
    suspend fun transcribe(audio: File, language: String = "zh"): String? = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || !audio.exists() || audio.length() == 0L) return@withContext null
        try {
            val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", audio.name, audio.asRequestBody("audio/mp4".toMediaTypeOrNull()))
                .addFormDataPart("language", language)
                .build()
            val req = Request.Builder()
                .url("$base/api/stt")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val txt = JSONObject(resp.body?.string() ?: "{}").optString("text", "").trim()
                txt.ifBlank { null }
            }
        } catch (_: Exception) {
            null
        }
    }
}
