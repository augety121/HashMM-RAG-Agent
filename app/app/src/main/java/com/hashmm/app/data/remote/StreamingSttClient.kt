package com.hashmm.app.data.remote

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.sqrt

/**
 * 边录边转（流式 STT）：AudioRecord 采 PCM16/16k/mono，经 WebSocket 持续发到后端 /api/stt/stream，
 * 后端每攒够 ~1s 回一段 partial；松开发 END → 收 final。所有回调都切回主线程，可直接更新 UI。
 * 不依赖 Context（AudioRecord/OkHttp 都不需要）。起不来返回 false，调用方走兜底。
 */
@Singleton
class StreamingSttClient @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = SharedHttp.base.newBuilder()
        .pingInterval(20, TimeUnit.SECONDS)
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val main = android.os.Handler(android.os.Looper.getMainLooper())
    private var ws: WebSocket? = null
    @Volatile private var running = false

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

    /** 开始流式识别（回调都在主线程）。返回 false=没起来，调用方兜底。 */
    @SuppressLint("MissingPermission")
    suspend fun start(
        onPartial: (String) -> Unit,
        onAmp: (Float) -> Unit,
        onFinal: (String?) -> Unit,
    ): Boolean = withContext(Dispatchers.IO) {
        if (running) return@withContext false
        val httpBase = base()
        val token = auth.currentToken()
        if (httpBase.isBlank() || token.isNullOrBlank()) return@withContext false
        val wsBase = httpBase.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://")
        // V306 修 APP-P0-01：令牌走 Authorization 头，不再拼进 WS URL（不进服务端 access log / 代理日志）。
        val url = "$wsBase/api/stt/stream"

        val sampleRate = 16000
        val minBuf = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuf <= 0) return@withContext false
        val recorder = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuf, sampleRate))
        } catch (_: Exception) {
            return@withContext false
        }
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            try { recorder.release() } catch (_: Exception) {}
            return@withContext false
        }

        var finalDelivered = false
        fun deliverFinal(t: String?) {
            if (finalDelivered) return
            finalDelivered = true
            main.post { onFinal(t) }
        }
        val listener = object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val o = JSONObject(text)
                    when {
                        o.has("partial") -> { val p = o.optString("partial"); main.post { onPartial(p) } }
                        o.has("final") -> deliverFinal(o.optString("final").ifBlank { null })
                        o.has("error") -> deliverFinal(null)
                    }
                } catch (_: Exception) {}
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { deliverFinal(null) }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                running = false
                deliverFinal(null)
            }
        }
        running = true
        ws = http.newWebSocket(
            Request.Builder().url(url)
                .header("Authorization", "Bearer $token")   // V306：令牌走头，不进 URL
                .build(),
            listener
        )
        Thread {
            try {
                recorder.startRecording()
                val buf = ByteArray(3200)   // ~100ms @ 16k pcm16
                while (running) {
                    val n = recorder.read(buf, 0, buf.size)
                    if (n > 0) {
                        ws?.send(buf.toByteString(0, n))
                        var sum = 0.0
                        var i = 0
                        while (i + 1 < n) {
                            val sample = (((buf[i + 1].toInt() shl 8) or (buf[i].toInt() and 0xff)).toShort()).toInt()
                            sum += sample.toDouble() * sample.toDouble()
                            i += 2
                        }
                        val rms = sqrt(sum / (n / 2).coerceAtLeast(1))
                        val level = (rms / 8000.0).coerceIn(0.0, 1.0).toFloat()
                        main.post { onAmp(level) }
                    }
                }
            } catch (_: Exception) {
            } finally {
                try { recorder.stop() } catch (_: Exception) {}
                try { recorder.release() } catch (_: Exception) {}
            }
        }.start()
        true
    }

    /** 松开：停采集 + 发 END，final 通过回调回来。 */
    fun stop() {
        running = false
        try { ws?.send("END") } catch (_: Exception) {}
    }

    /** 取消：停采集 + 发 CANCEL + 关闭，不出 final。 */
    fun cancel() {
        running = false
        try { ws?.send("CANCEL") } catch (_: Exception) {}
        try { ws?.close(1000, null) } catch (_: Exception) {}
        ws = null
    }
}
