package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

@Serializable
private data class RuntimeProviderDto(
    val id: String = "",
    val configured: Boolean = false,
    val enabled: Boolean = false,
    val preview: Boolean = false,
    val problems: List<String> = emptyList(),
    val reachable: Boolean = false,
    val ready: Boolean = false,
)

@Serializable
private data class RuntimeProvidersDto(
    val providers: List<RuntimeProviderDto> = emptyList(),
)

data class CloudWorkspaceState(
    val configured: Boolean = false,
    val ready: Boolean = false,
    val preview: Boolean = true,
    val reason: String = "not_configured",
)

/** Read-only App projection; Cloudflare credentials never leave the server. */
@Singleton
class WorkspaceRuntimeRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val client = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .callTimeout(12, TimeUnit.SECONDS)
        .build()

    suspend fun cloudWorkspace(): CloudWorkspaceState = withContext(Dispatchers.IO) {
        val base = settings.clientUrl.first().trim().trimEnd('/').toHttpUrlOrNull()
            ?: return@withContext CloudWorkspaceState(reason = "backend_not_configured")
        val token = auth.currentToken()?.takeIf { it.isNotBlank() }
            ?: return@withContext CloudWorkspaceState(reason = "authentication_required")
        val listRequest = Request.Builder()
            .url(base.newBuilder().addPathSegments("api/v3/runtime/providers").build())
            .header("Authorization", "Bearer $token").get().build()
        val provider = runCatching {
            client.newCall(listRequest).execute().use { response ->
                if (!response.isSuccessful) return@use null
                response.body?.string()?.let { json.decodeFromString<RuntimeProvidersDto>(it) }
                    ?.providers?.firstOrNull { it.id == "cloudflare_computer_worker_shell" }
            }
        }.getOrNull() ?: return@withContext CloudWorkspaceState(reason = "backend_unreachable")
        if (!provider.configured) {
            return@withContext CloudWorkspaceState(
                configured = false,
                ready = false,
                preview = provider.preview,
                reason = provider.problems.firstOrNull() ?: "not_configured",
            )
        }
        val healthRequest = Request.Builder()
            .url(base.newBuilder().addPathSegments("api/v3/runtime/providers/cloudflare-computer/health").build())
            .header("Authorization", "Bearer $token").get().build()
        val health = runCatching {
            client.newCall(healthRequest).execute().use { response ->
                if (!response.isSuccessful) return@use null
                response.body?.string()?.let { json.decodeFromString<RuntimeProviderDto>(it) }
            }
        }.getOrNull()
        CloudWorkspaceState(
            configured = true,
            ready = health?.let { it.reachable && it.ready } == true,
            preview = provider.preview,
            reason = if (health == null) "health_check_failed" else if (health.ready) "ready" else "not_ready",
        )
    }
}
