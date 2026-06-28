package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class KGNode(
    val id: String,
    val label: String,
    val type: String,
    val color: String,
    val size: Int,
)

data class KGEdge(val from: String, val to: String, val label: String)

data class KGGraph(
    val nodes: List<KGNode> = emptyList(),
    val edges: List<KGEdge> = emptyList(),
    val entities: Int = 0,
    val relations: Int = 0,
    val communities: Int = 0,
    val error: String? = null,
)

/**
 * 知识图谱：GET {base}/api/kg/graph?max_nodes=N（公开，无需鉴权）。
 * 节点 {id,label,type,color,size}，边 {from,to,label}，stats {entities,relations,communities}。
 */
@Singleton
class KGRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()

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

    suspend fun graph(maxNodes: Int = 60): KGGraph = withContext(Dispatchers.IO) {
        val base = base()
        if (base.isBlank()) return@withContext KGGraph(error = "未连接客户端后端")
        try {
            val rb = Request.Builder().url("$base/api/kg/graph?max_nodes=$maxNodes").get()
            auth.currentToken()?.let { if (it.isNotBlank()) rb.header("Authorization", "Bearer $it") }
            http.newCall(rb.build()).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext KGGraph(error = "加载失败（${resp.code}）")
                val o = JSONObject(resp.body?.string() ?: return@withContext KGGraph(error = "空响应"))
                val nodesArr = o.optJSONArray("nodes")
                val edgesArr = o.optJSONArray("edges")
                val stats = o.optJSONObject("stats")
                val nodes = if (nodesArr == null) emptyList() else (0 until nodesArr.length()).mapNotNull { i ->
                    val n = nodesArr.optJSONObject(i) ?: return@mapNotNull null
                    KGNode(
                        id = n.optString("id"),
                        label = n.optString("label", n.optString("id")),
                        type = n.optString("type", "概念"),
                        color = n.optString("color", "#71717a"),
                        size = n.optInt("size", 12),
                    )
                }
                val edges = if (edgesArr == null) emptyList() else (0 until edgesArr.length()).mapNotNull { i ->
                    val e = edgesArr.optJSONObject(i) ?: return@mapNotNull null
                    KGEdge(from = e.optString("from"), to = e.optString("to"), label = e.optString("label", ""))
                }
                KGGraph(
                    nodes = nodes,
                    edges = edges,
                    entities = stats?.optInt("entities", nodes.size) ?: nodes.size,
                    relations = stats?.optInt("relations", edges.size) ?: edges.size,
                    communities = stats?.optInt("communities", 0) ?: 0,
                    error = if (nodes.isEmpty()) "图谱为空（尚未构建实体）" else null,
                )
            }
        } catch (e: Exception) {
            KGGraph(error = "网络错误")
        }
    }
}
