package com.hashmm.app.data.remote

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.postgrest.postgrest
import io.github.jan.supabase.postgrest.query.Columns
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 头像：以 Supabase profiles.avatar_url 为「始终可达」的权威存储（小图 data URL），
 * 后端 /api/profile/avatar 仅作向后兼容的次选。
 *
 * 修复甲方反馈「头像过一会变默认」：根因是旧实现只存 AutoDL 后端磁盘，后端重启/旧版/换设备即丢。
 * 现在：上传 → 压成 ≤256px JPEG → 写入 Supabase（登录态 RLS 仅本人）；读取优先 Supabase。
 */
@Singleton
class AvatarRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
    private val supabase: SupabaseClient,
) {
    private val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()
    private val jpeg = "image/jpeg".toMediaTypeOrNull()

    @Serializable
    private data class AvatarRow(@SerialName("avatar_url") val avatarUrl: String? = null)

    @Serializable
    private data class AvatarPatch(@SerialName("avatar_url") val avatarUrl: String)

    private suspend fun base(): String = settings.clientUrl.first().trim().trimEnd('/')

    /** 把原图压成 ≤256px 的 JPEG 字节（控制写入 Supabase 的体积，约 20–40KB）。 */
    private fun compress(bytes: ByteArray, max: Int = 256, quality: Int = 72): ByteArray {
        val src = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return bytes
        val scale = minOf(1f, max.toFloat() / maxOf(src.width, src.height))
        val w = maxOf(1, (src.width * scale).toInt())
        val h = maxOf(1, (src.height * scale).toInt())
        val scaled = if (scale < 1f) Bitmap.createScaledBitmap(src, w, h, true) else src
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, quality, out)
        return out.toByteArray()
    }

    private fun toDataUrl(jpegBytes: ByteArray): String =
        "data:image/jpeg;base64," + Base64.encodeToString(jpegBytes, Base64.NO_WRAP)

    private fun fromDataUrl(dataUrl: String): ByteArray? = try {
        val b64 = dataUrl.substringAfter("base64,", "")
        if (b64.isBlank()) null else Base64.decode(b64, Base64.DEFAULT)
    } catch (_: Exception) { null }

    /** 上传当前用户头像。先写 Supabase（权威），再兼容写后端。返回是否至少一处成功。 */
    suspend fun upload(bytes: ByteArray): Boolean = withContext(Dispatchers.IO) {
        val uid = auth.currentUserId()
        val small = compress(bytes)
        var ok = false

        // 1) Supabase（始终可达；RLS 仅本人可写自己的档案）
        if (!uid.isNullOrBlank()) {
            ok = try {
                supabase.postgrest.from("profiles").update(AvatarPatch(toDataUrl(small))) {
                    filter { eq("id", uid) }
                }
                true
            } catch (_: Exception) { false }
        }

        // 2) 后端中转（向后兼容；失败不影响）
        val baseUrl = base()
        val token = auth.currentToken()
        if (baseUrl.isNotBlank() && !token.isNullOrBlank()) {
            try {
                val rb = Request.Builder().url("$baseUrl/api/profile/avatar")
                    .post(small.toRequestBody(jpeg))
                    .header("Authorization", "Bearer $token")
                http.newCall(rb.build()).execute().use { if (it.isSuccessful) ok = true }
            } catch (_: Exception) { /* 忽略 */ }
        }
        ok
    }

    /** 读取指定 uid 的头像字节：优先 Supabase（权威），失败再退后端。无则 null。 */
    suspend fun load(uid: String): ByteArray? = withContext(Dispatchers.IO) {
        if (uid.isBlank()) return@withContext null

        // 1) Supabase profiles.avatar_url（data URL → 字节）
        try {
            val rows = supabase.postgrest.from("profiles")
                .select(Columns.list("avatar_url")) { filter { eq("id", uid) }; limit(1) }
                .decodeList<AvatarRow>()
            val url = rows.firstOrNull()?.avatarUrl
            if (!url.isNullOrBlank()) fromDataUrl(url)?.let { return@withContext it }
        } catch (_: Exception) { /* 离线/未部署 SQL：退后端 */ }

        // 2) 后端中转
        val baseUrl = base()
        if (baseUrl.isBlank()) return@withContext null
        try {
            val rb = Request.Builder().url("$baseUrl/api/profile/avatar/$uid").get()
            val token = auth.currentToken()
            if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
            http.newCall(rb.build()).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                resp.body?.bytes()
            }
        } catch (_: Exception) { null }
    }
}
