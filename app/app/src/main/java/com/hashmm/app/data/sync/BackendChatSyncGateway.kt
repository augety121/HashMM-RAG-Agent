package com.hashmm.app.data.sync

import com.hashmm.app.BuildConfig
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.remote.SharedHttp
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.security.MessageDigest
import java.net.URI
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class BackendConversationSync(
    val conversations: List<ChatConversation>,
    val tombstoneIds: Set<String>,
    val tombstoneCursor: Double,
    val complete: Boolean,
)

internal data class BackendTombstonePage(
    val ids: Set<String>,
    val nextSince: Double,
    val nextAfterId: String,
    val hasMore: Boolean,
)

/**
 * Canonical conversation sync goes through the owner-checked HashMM API.
 * Supabase Realtime remains a wake-up signal and the old direct table reads
 * remain a bounded compatibility fallback in [SyncRepository].
 */
@Singleton
class BackendChatSyncGateway @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    @Volatile
    var lastConversationFailure: String? = null
        private set

    private val client = SharedHttp.base.newBuilder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS)
        .build()

    suspend fun conversations(tombstoneSince: Double): BackendConversationSync? = withContext(Dispatchers.IO) {
        val base = settings.clientUrl.first().trim().trimEnd('/').toHttpUrlOrNull()
            ?: return@withContext fail("invalid_backend_url")
        val token = auth.currentToken()?.takeIf { it.isNotBlank() }
            ?: return@withContext fail("authentication_required")
        lastConversationFailure = null
        if (!verifyBootstrap(base.toString().trimEnd('/'), token)) return@withContext null
        val all = LinkedHashMap<String, ChatConversation>()
        var cursor = ""
        var complete = false
        for (pageIndex in 0 until MAX_PAGES) {
            val url = base.newBuilder().addPathSegments("api/conversations")
                .addQueryParameter("limit", "500")
                .addQueryParameter("include_empty", "1")
                .addQueryParameter("scope", "all")
                .apply { if (cursor.isNotBlank()) addQueryParameter("cursor", cursor) }
                .build()
            val raw = get(url.toString(), token) ?: return@withContext fail(lastConversationFailure ?: "network_error")
            val page = parseBackendConversations(raw) ?: return@withContext fail("invalid_conversation_payload")
            page.first.forEach { all[it.id] = it }
            if (!page.second) {
                complete = true
                break
            }
            val next = page.third
            if (next.isBlank() || next == cursor) return@withContext fail("invalid_conversation_cursor")
            cursor = next
        }
        if (!complete) return@withContext fail("conversation_page_limit")

        val deleted = linkedSetOf<String>()
        var tombstoneCursor = tombstoneSince.coerceAtLeast(0.0)
        var afterId = ""
        var tombstonesComplete = false
        for (pageIndex in 0 until MAX_PAGES) {
            val tombstoneUrl = base.newBuilder().addPathSegments("api/conversations/tombstones")
                .addQueryParameter("since", tombstoneCursor.toString())
                .addQueryParameter("after_id", afterId)
                .addQueryParameter("limit", "500")
                .build()
            val raw = get(tombstoneUrl.toString(), token) ?: return@withContext fail(lastConversationFailure ?: "network_error")
            val page = parseBackendTombstones(raw) ?: return@withContext fail("invalid_tombstone_payload")
            deleted.addAll(page.ids)
            if (!page.hasMore) {
                tombstoneCursor = page.nextSince
                tombstonesComplete = true
                break
            }
            if (page.nextSince < tombstoneCursor ||
                (page.nextSince == tombstoneCursor && page.nextAfterId <= afterId)
            ) return@withContext fail("invalid_tombstone_cursor")
            tombstoneCursor = page.nextSince
            afterId = page.nextAfterId
        }
        if (!tombstonesComplete) return@withContext fail("tombstone_page_limit")
        lastConversationFailure = null
        BackendConversationSync(all.values.toList(), deleted, tombstoneCursor, complete = true)
    }

    suspend fun messages(convId: String): List<ChatMessage>? = withContext(Dispatchers.IO) {
        if (convId.isBlank()) return@withContext null
        val base = settings.clientUrl.first().trim().trimEnd('/').toHttpUrlOrNull()
            ?: return@withContext null
        val token = auth.currentToken()?.takeIf { it.isNotBlank() } ?: return@withContext null
        val all = LinkedHashMap<String, ChatMessage>()
        var before: String? = null
        var complete = false
        for (pageIndex in 0 until MAX_PAGES) {
            val url = base.newBuilder()
                .addPathSegments("api/conversations")
                .addPathSegment(convId)
                .addPathSegment("messages")
                .addQueryParameter("limit", "500")
                .apply { before?.let { addQueryParameter("before_ts", it) } }
                .build()
            val raw = get(url.toString(), token) ?: return@withContext null
            val page = parseBackendMessages(raw) ?: return@withContext null
            page.first.forEach { all[it.id] = it }
            if (!page.second) {
                complete = true
                break
            }
            val next = page.third
            if (next.isNullOrBlank() || next == before) return@withContext null
            before = next
        }
        if (!complete) return@withContext null
        all.values.toList()
    }

    suspend fun appendMessages(convId: String, rows: List<ChatMessage>): Boolean = withContext(Dispatchers.IO) {
        if (convId.isBlank() || rows.isEmpty()) return@withContext true
        val base = settings.clientUrl.first().trim().trimEnd('/').toHttpUrlOrNull()
            ?: return@withContext false
        val token = auth.currentToken()?.takeIf { it.isNotBlank() } ?: return@withContext false
        for (message in rows) {
            val endpoint = when (message.role) {
                "user" -> "user-message"
                "assistant" -> "assistant-message"
                else -> continue
            }
            val url = base.newBuilder()
                .addPathSegments("api/conversations")
                .addPathSegment(convId)
                .addPathSegment(endpoint)
                .build()
            val body = buildJsonObject {
                put("client_message_id", message.id)
                put("content", message.content)
                message.files?.let { put("files", it) }
                message.sources?.let { put("sources", it) }
                message.toolCalls?.let { put("tool_calls", it) }
            }.toString().toRequestBody(JSON_MEDIA_TYPE)
            val request = identifiedRequest(url.toString(), token)
                .post(body).build()
            val ok = runCatching {
                client.newCall(request).execute().use { response ->
                    response.isSuccessful || response.code == 409
                }
            }.getOrDefault(false)
            if (!ok) return@withContext false
        }
        true
    }

    private fun fail(reason: String): BackendConversationSync? {
        lastConversationFailure = reason
        return null
    }

    private suspend fun verifyBootstrap(base: String, token: String): Boolean {
        val raw = get("$base/api/client/bootstrap", token) ?: return false
        val root = runCatching { gatewayJson.parseToJsonElement(raw).jsonObject }.getOrNull()
            ?: return failBootstrap("invalid_bootstrap_payload")
        if (root.text("sync_protocol") != "hashmm.sync.v2") {
            return failBootstrap("sync_protocol_mismatch")
        }
        val expectedProject = runCatching {
            val host = URI(BuildConfig.SUPABASE_URL).host.orEmpty().lowercase()
            host.removeSuffix(".supabase.co")
        }.getOrDefault("")
        if (expectedProject.isNotBlank() && root.text("supabase_project_ref") != expectedProject) {
            return failBootstrap("identity_project_mismatch")
        }
        val expectedFingerprint = auth.currentUserId()?.let(::fingerprint).orEmpty()
        if (expectedFingerprint.isNotBlank() && root.text("user_sub_fingerprint") != expectedFingerprint) {
            return failBootstrap("identity_subject_mismatch")
        }
        return true
    }

    private fun failBootstrap(reason: String): Boolean {
        lastConversationFailure = reason
        return false
    }

    private fun fingerprint(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
        .take(12)

    private suspend fun identifiedRequest(url: String, token: String): Request.Builder = Request.Builder()
        .url(url)
        .header("Authorization", "Bearer $token")
        .header("Accept", "application/json")
        .header("X-HashMM-Client", "android")
        .header("X-Client-Instance", settings.clientInstanceId())
        .header("X-App-Version", BuildConfig.VERSION_NAME)
        .header("X-Build-Channel", if (BuildConfig.DEBUG) "debug" else "release")
        .header("X-Request-ID", UUID.randomUUID().toString())

    private suspend fun get(url: String, token: String): String? {
        suspend fun execute(accessToken: String): Pair<Int, String?> = runCatching {
            val request = identifiedRequest(url, accessToken).get().build()
            client.newCall(request).execute().use { response ->
                response.code to if (response.isSuccessful) {
                    response.body?.string()?.takeIf { it.length <= MAX_RESPONSE_CHARS }
                } else null
            }
        }.getOrElse { -1 to null }

        var (status, body) = execute(token)
        if (status == 401) {
            val refreshed = auth.refreshAccessToken()
            if (!refreshed.isNullOrBlank()) {
                val retried = execute(refreshed)
                status = retried.first
                body = retried.second
            }
        }
        if (body != null) return body
        lastConversationFailure = when (status) {
            401 -> "authentication_expired"
            403 -> "forbidden"
            404 -> "server_upgrade_required"
            in 500..599 -> "server_error"
            -1 -> "network_error"
            else -> "http_$status"
        }
        return null
    }

    companion object {
        private const val MAX_PAGES = 20
        private const val MAX_RESPONSE_CHARS = 8 * 1024 * 1024
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

private val gatewayJson = Json { ignoreUnknownKeys = true }

internal fun parseBackendConversations(raw: String): Triple<List<ChatConversation>, Boolean, String>? = runCatching {
    val root = gatewayJson.parseToJsonElement(raw).jsonObject
    val rows = root["conversations"]?.jsonArray.orEmpty()
    val items = buildList {
        for (element in rows) {
            val row = element.jsonObject
            val id = row.text("id").trim()
            if (id.isBlank()) continue
            add(ChatConversation(
                id = id,
                userId = row.text("user_id"),
                title = row.text("title").ifBlank { "新对话" },
                pinned = row.bool("pinned"),
                metadata = row.elementOrNull("metadata"),
                createdAt = row.text("created_at"),
                updatedAt = row.text("updated_at"),
                lastMessageAt = row.text("last_message_at").ifBlank { row.text("last_activity_at") },
            ))
        }
    }
    val page = root["page"]?.jsonObject ?: JsonObject(emptyMap())
    Triple(items, page.bool("has_more"), page.text("next_cursor"))
}.getOrNull()

internal fun parseBackendTombstones(raw: String): BackendTombstonePage? = runCatching {
    val root = gatewayJson.parseToJsonElement(raw).jsonObject
    val rows = root["tombstones"]?.jsonArray.orEmpty()
    val ids = buildSet {
        for (element in rows) {
            val row = element.jsonObject
            val id = row.text("conversation_id").ifBlank {
                row.text("conv_id").ifBlank { row.text("id") }
            }.trim()
            if (id.isNotBlank()) add(id)
        }
    }
    BackendTombstonePage(
        ids = ids,
        nextSince = root.text("next_since").toDoubleOrNull() ?: 0.0,
        nextAfterId = root.text("next_after_id"),
        hasMore = root.bool("has_more"),
    )
}.getOrNull()

internal fun parseBackendMessages(raw: String): Triple<List<ChatMessage>, Boolean, String?>? = runCatching {
    val root = gatewayJson.parseToJsonElement(raw).jsonObject
    val rows = root["messages"]?.jsonArray.orEmpty()
    val items = buildList {
        for (element in rows) {
            val row = element.jsonObject
            val id = row.text("id").trim()
            if (id.isBlank()) continue
            add(ChatMessage(
                id = id,
                convId = row.text("conv_id"),
                userId = row.text("user_id"),
                role = row.text("role").ifBlank { "user" },
                content = row.text("content"),
                thinking = row.text("thinking"),
                toolCalls = row.elementOrNull("tool_calls"),
                files = row.elementOrNull("files"),
                sources = row.elementOrNull("sources"),
                groundings = row.elementOrNull("groundings"),
                runManifest = row.elementOrNull("run_manifest"),
                suggestions = row.elementOrNull("suggestions"),
                status = row.text("status").ifBlank { "complete" },
                feedback = row.text("feedback"),
                tokensIn = row["tokens_in"]?.jsonPrimitive?.intOrNull ?: 0,
                tokensOut = row["tokens_out"]?.jsonPrimitive?.intOrNull ?: 0,
                createdAt = row.text("created_at"),
                updatedAt = row.text("updated_at"),
            ))
        }
    }
    val page = root["page"]?.jsonObject ?: JsonObject(emptyMap())
    Triple(items, page.bool("has_more"), page.text("oldest_created_at").ifBlank { null })
}.getOrNull()

private fun JsonObject.text(key: String): String =
    this[key]?.takeUnless { it is JsonNull }?.jsonPrimitive?.contentOrNull.orEmpty()

private fun JsonObject.bool(key: String): Boolean =
    when (this[key]?.jsonPrimitive?.contentOrNull?.trim()?.lowercase()) {
        "true", "1" -> true
        else -> false
    }

private fun JsonObject.elementOrNull(key: String): JsonElement? =
    this[key]?.takeUnless { it is JsonNull }
