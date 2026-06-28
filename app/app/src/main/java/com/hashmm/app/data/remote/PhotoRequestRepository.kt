package com.hashmm.app.data.remote

import android.content.ContentResolver
import android.content.ContentUris
import android.provider.MediaStore
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/** 一条"电脑端向手机要照片"的待办请求。 */
data class PhoneRequest(val id: String, val convId: String, val query: String)

/**
 * 手机端消费"电脑→手机取照片"请求：
 *  1) 轮询后端 GET /api/phone-file-requests 取待办（target=phone, pending）
 *  2) 用户同意后，取相册最近一张照片 → 上传到该会话 → 贴回一条带文件卡片的助手消息 → 标记完成
 * 与桌面投送同一套上传/贴消息端点，所以照片会像普通文件一样出现在电脑端对话里。
 */
@Singleton
class PhotoRequestRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .callTimeout(60, TimeUnit.SECONDS)
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

    /** 轮询待办（失败返回空，绝不抛错——本功能锦上添花，不能影响 App 其它部分）。 */
    suspend fun pollPending(): List<PhoneRequest> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext emptyList()
        try {
            val req = Request.Builder().url("$base/api/phone-file-requests")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext emptyList()
                val body = resp.body?.string() ?: return@withContext emptyList()
                val arr = JSONObject(body).optJSONArray("requests") ?: return@withContext emptyList()
                val out = ArrayList<PhoneRequest>()
                for (i in 0 until arr.length()) {
                    val o = arr.optJSONObject(i) ?: continue
                    val id = o.optString("id"); val conv = o.optString("conv_id")
                    if (id.isNotBlank() && conv.isNotBlank()) out.add(PhoneRequest(id, conv, o.optString("query")))
                }
                out
            }
        } catch (_: Exception) { emptyList() }
    }

    /** 回写请求状态（processing/done/denied/error）。 */
    suspend fun setStatus(id: String, status: String) = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || id.isBlank()) return@withContext
        try {
            val payload = JSONObject().put("status", status).toString()
                .toRequestBody("application/json".toMediaTypeOrNull())
            val req = Request.Builder().url("$base/api/phone-file-requests/$id/status")
                .header("Authorization", "Bearer $token").post(payload).build()
            http.newCall(req).execute().use { }
        } catch (_: Exception) {}
    }

    /** 取相册最近一张照片 → 上传 → 贴回会话 → 标记完成。返回是否成功。 */
    suspend fun fulfill(req: PhoneRequest, cr: ContentResolver): Boolean = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        setStatus(req.id, "processing")
        // 1) 相册最近一张
        val photo = latestImage(cr)
        if (photo == null) {
            postMsg(base, token, req.convId, "没在手机相册里找到照片。", null)
            setStatus(req.id, "error"); return@withContext false
        }
        val (name, bytes) = photo
        try {
            // 2) 上传到会话工作区
            val mb = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", name, bytes.toRequestBody("image/*".toMediaTypeOrNull()))
                .build()
            val upReq = Request.Builder().url("$base/api/conversations/${req.convId}/upload")
                .header("Authorization", "Bearer $token").post(mb).build()
            val (fn, rel) = http.newCall(upReq).execute().use { resp ->
                if (!resp.isSuccessful) throw RuntimeException("upload HTTP ${resp.code}")
                val j = JSONObject(resp.body?.string() ?: "{}")
                Pair(j.optString("filename", name), j.optString("download_url"))
            }
            // 3) 拼完整可点链接（下载端点支持 ?token 鉴权），贴回一条文件卡片消息
            val full = if (rel.isNotBlank()) base + rel + (if (rel.contains("?")) "&" else "?") +
                "token=" + URLEncoder.encode(token, "UTF-8") else ""
            val files = JSONArray().put(
                JSONObject().put("filename", fn).put("download_url", full).put("size", bytes.size)
            )
            postMsg(base, token, req.convId, "已从手机发送照片：$fn", files)
            setStatus(req.id, "done")
            true
        } catch (e: Exception) {
            postMsg(base, token, req.convId, "找到了照片但发送失败（${e.message}），稍后再试。", null)
            setStatus(req.id, "error"); false
        }
    }

    /** 贴一条助手消息（可带文件卡片）到会话——和桌面投送同一端点。 */
    private fun postMsg(base: String, token: String, convId: String, content: String, files: JSONArray?) {
        try {
            val payload = JSONObject().put("content", content)
            if (files != null && files.length() > 0) payload.put("files", files)
            val req = Request.Builder().url("$base/api/conversations/$convId/assistant-message")
                .header("Authorization", "Bearer $token")
                .post(payload.toString().toRequestBody("application/json".toMediaTypeOrNull())).build()
            http.newCall(req).execute().use { }
        } catch (_: Exception) {}
    }

    /** 查询相册里最新加入的一张图片，返回(显示名, 字节)。无则 null。 */
    private fun latestImage(cr: ContentResolver): Pair<String, ByteArray>? {
        return try {
            val proj = arrayOf(MediaStore.Images.Media._ID, MediaStore.Images.Media.DISPLAY_NAME)
            val sort = "${MediaStore.Images.Media.DATE_ADDED} DESC"
            cr.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, proj, null, null, sort)?.use { c ->
                if (!c.moveToFirst()) return null
                val id = c.getLong(c.getColumnIndexOrThrow(MediaStore.Images.Media._ID))
                val nm = c.getString(c.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME))
                    ?: "photo_$id.jpg"
                val uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id)
                val bytes = cr.openInputStream(uri)?.use { it.readBytes() } ?: return null
                Pair(nm, bytes)
            }
        } catch (_: Exception) { null }
    }
}
