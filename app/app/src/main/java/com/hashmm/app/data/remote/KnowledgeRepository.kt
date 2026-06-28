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
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/** 语料/知识库统计（对应客户端首页那串「N 个知识切片」）。 */
data class CorpusStats(
    val totalChunks: Int = 0,
    val hashBits: Int = 0,
    val indexSizeKb: Int = 0,
    val llmReady: Boolean = false,
    val activeModel: String = "",
    val modalities: Map<String, Int> = emptyMap(),
)

/** 一篇已索引文档。 */
data class KbDoc(
    val docId: String,
    val filename: String,
    val chunks: Int,
    val modalities: List<String> = emptyList(),
)

/** 文档时效记录（失效区/归档区）。日期为 unix 秒；null 表示不限。 */
data class DocValidity(
    val filename: String,
    val docId: String = "",
    val status: String = "active",   // active / expired / archived / pending
    val effectiveDate: Double? = null,
    val expiryDate: Double? = null,
    val note: String = "",
)

/**
 * 知识库：直连客户端后端（clientUrl + Supabase token）拉取语料统计与已索引文档。
 * GET {base}/api/corpus/stats、GET {base}/api/kb/documents。任何失败安全返回空。
 */
@Singleton
class KnowledgeRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = OkHttpClient.Builder().callTimeout(12, TimeUnit.SECONDS).build()
    // 上传 + 解析索引可能较久，给足超时
    private val uploadHttp = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .build()

    private suspend fun base(): String {
        val b = settings.clientUrl.first().trim().trimEnd('/')
        if (b.isBlank()) return ""
        val schemeless = b.removePrefix("https://").removePrefix("http://")
        val host = schemeless.substringBefore("/").substringBefore(":")
        val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(host)
        return when {
            isIp -> "http://$schemeless"               // IP 一律 http（无有效证书）
            b.startsWith("http") -> b
            else -> "https://$b"
        }
    }

    suspend fun stats(): CorpusStats? = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank()) return@withContext null
        try {
            val rb = Request.Builder().url("$base/api/corpus/stats").get()
            if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
            http.newCall(rb.build()).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val o = JSONObject(resp.body?.string() ?: return@withContext null)
                val mods = mutableMapOf<String, Int>()
                o.optJSONObject("modalities")?.let { m -> val it = m.keys(); while (it.hasNext()) { val k = it.next(); mods[k] = m.optInt(k) } }
                CorpusStats(
                    totalChunks = o.optInt("total_chunks"),
                    hashBits = o.optInt("hash_bits"),
                    indexSizeKb = o.optInt("index_size_kb"),
                    llmReady = o.optBoolean("llm_ready"),
                    activeModel = o.optString("active_model", ""),
                    modalities = mods,
                )
            }
        } catch (e: Exception) { null }
    }

    suspend fun documents(): List<KbDoc> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank()) return@withContext emptyList()
        try {
            val rb = Request.Builder().url("$base/api/kb/documents").get()
            if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
            http.newCall(rb.build()).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext emptyList()
                val o = JSONObject(resp.body?.string() ?: return@withContext emptyList())
                val arr = o.optJSONArray("documents") ?: return@withContext emptyList()
                (0 until arr.length()).mapNotNull { i ->
                    val d = arr.optJSONObject(i) ?: return@mapNotNull null
                    val modsArr = d.optJSONArray("modalities")
                    val mods = if (modsArr != null) (0 until modsArr.length()).map { modsArr.optString(it) } else emptyList()
                    KbDoc(
                        docId = d.optString("doc_id", d.optString("id", "")),
                        filename = d.optString("filename", d.optString("name", "未命名文档")),
                        chunks = d.optInt("num_chunks", d.optInt("chunks", 0)),
                        modalities = mods,
                    )
                }
            }
        } catch (e: Exception) { emptyList() }
    }

    /**
     * 上传文档进知识库：multipart POST {base}/api/kb/upload，字段名 "file"。
     * 返回 (是否成功, 提示信息)。
     */
    suspend fun uploadDocument(filename: String, bytes: ByteArray, mime: String): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank()) return@withContext false to "未连接客户端后端"
        if (bytes.isEmpty()) return@withContext false to "文件为空"
        try {
            val safeMime = mime.ifBlank { "application/octet-stream" }
            val part = bytes.toRequestBody(safeMime.toMediaTypeOrNull())
            val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", filename, part)
                .build()
            val rb = Request.Builder().url("$base/api/kb/upload").post(body)
            if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
            uploadHttp.newCall(rb.build()).execute().use { resp ->
                val txt = resp.body?.string().orEmpty()
                if (resp.code == 403) return@withContext false to "需要管理员权限"
                if (!resp.isSuccessful) return@withContext false to "上传失败（${resp.code}）"
                // 后端常返回 {chunks: N} 或 {message: ...}
                val chunks = runCatching { JSONObject(txt).optInt("chunks", -1) }.getOrDefault(-1)
                val msg = if (chunks >= 0) "已索引 $chunks 个切片" else "上传成功，正在索引"
                true to msg
            }
        } catch (e: Exception) {
            false to "上传出错：网络或超时"
        }
    }

    // ── 文档时效性 / 失效区 / 归档 ──
    private val jsonMedia = "application/json".toMediaTypeOrNull()

    private suspend fun postJson(path: String, body: JSONObject): JSONObject? = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank()) return@withContext null
        try {
            val rb = Request.Builder().url("$base$path")
                .post(body.toString().toRequestBody(jsonMedia))
            if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
            http.newCall(rb.build()).execute().use { resp ->
                val txt = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) return@withContext null
                runCatching { JSONObject(txt) }.getOrNull()
            }
        } catch (e: Exception) { null }
    }

    /** 失效区/归档区列表。status = expired/archived/active/全部(空)。 */
    suspend fun validityList(status: String): List<DocValidity> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank()) return@withContext emptyList()
        try {
            val q = if (status.isBlank()) "" else "?status=$status"
            val rb = Request.Builder().url("$base/api/kb/validity/list$q").get()
            if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
            http.newCall(rb.build()).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext emptyList()
                val o = JSONObject(resp.body?.string() ?: return@withContext emptyList())
                val arr = o.optJSONArray("items") ?: return@withContext emptyList()
                (0 until arr.length()).mapNotNull { i ->
                    val d = arr.optJSONObject(i) ?: return@mapNotNull null
                    DocValidity(
                        filename = d.optString("filename"),
                        docId = d.optString("doc_id", ""),
                        status = d.optString("status", "active"),
                        effectiveDate = d.optDouble("effective_date").takeIf { !d.isNull("effective_date") },
                        expiryDate = d.optDouble("expiry_date").takeIf { !d.isNull("expiry_date") },
                        note = d.optString("note", ""),
                    )
                }
            }
        } catch (e: Exception) { emptyList() }
    }

    /** 设置/更新某文档有效期（日期 'YYYY-MM-DD'，空表示不限）。 */
    suspend fun setValidity(filename: String, effectiveDate: String?, expiryDate: String?, note: String): Pair<Boolean, String> {
        val body = JSONObject().put("filename", filename)
        if (!effectiveDate.isNullOrBlank()) body.put("effective_date", effectiveDate)
        if (!expiryDate.isNullOrBlank()) body.put("expiry_date", expiryDate)
        if (note.isNotBlank()) body.put("note", note)
        val r = postJson("/api/kb/validity/set", body)
        return when {
            r == null -> false to "保存失败（需管理员或检查网络）"
            r.has("error") -> false to r.optString("error")
            else -> true to "已设置：${r.optString("status", "active")}"
        }
    }

    suspend fun archiveDoc(filename: String): Boolean =
        postJson("/api/kb/validity/archive", JSONObject().put("filename", filename))?.optBoolean("ok") == true

    suspend fun restoreDoc(filename: String): Boolean =
        postJson("/api/kb/validity/restore", JSONObject().put("filename", filename))?.optBoolean("ok") == true

    /** 扫描过期文档移入失效区，返回新失效数量。 */
    suspend fun sweepExpired(): Int =
        postJson("/api/kb/validity/sweep", JSONObject())?.optInt("count", 0) ?: 0
}
