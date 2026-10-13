package com.hashmm.app.ui.kg

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.KGGraph
import com.hashmm.app.data.remote.KGNode
import com.hashmm.app.data.remote.KGEdge
import com.hashmm.app.data.remote.KGRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt
import javax.inject.Inject

/** 归一化坐标点（范围约 [-1,1]）。 */
data class Pt(val x: Float, val y: Float)

data class KGUi(
    val loading: Boolean = true,
    val graph: KGGraph = KGGraph(),
    val positions: Map<String, Pt> = emptyMap(),
)

@HiltViewModel
class KGViewModel @Inject constructor(
    private val repo: KGRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(KGUi())
    val ui: StateFlow<KGUi> = _ui.asStateFlow()

    init { load() }

    fun load() {
        _ui.value = _ui.value.copy(loading = true)
        viewModelScope.launch {
            val g = repo.graph(maxNodes = 60)
            val pos = if (g.nodes.isNotEmpty()) withContext(Dispatchers.Default) { layout(g.nodes, g.edges) } else emptyMap()
            _ui.value = KGUi(loading = false, graph = g, positions = pos)
        }
    }

    /** 力导向布局（Fruchterman–Reingold 简化版），输出归一化坐标。 */
    private fun layout(nodes: List<KGNode>, edges: List<KGEdge>): Map<String, Pt> {
        val n = nodes.size
        if (n == 0) return emptyMap()
        val ids = nodes.map { it.id }
        val idx = ids.withIndex().associate { (i, id) -> id to i }
        val px = FloatArray(n); val py = FloatArray(n)
        for (i in 0 until n) {
            val a = 2.0 * PI * i / n
            px[i] = cos(a).toFloat(); py[i] = sin(a).toFloat()
        }
        val k = (2.0 / sqrt(n.toDouble())).toFloat()
        val pairs = edges.mapNotNull { e ->
            val a = idx[e.from]; val b = idx[e.to]
            if (a != null && b != null && a != b) a to b else null
        }
        var temp = 0.6f
        repeat(160) {
            val dx = FloatArray(n); val dy = FloatArray(n)
            // 斥力（所有点对）
            for (i in 0 until n) for (j in i + 1 until n) {
                var ddx = px[i] - px[j]; var ddy = py[i] - py[j]
                var dist = sqrt(ddx * ddx + ddy * ddy)
                if (dist < 0.001f) { dist = 0.001f; ddx = 0.001f }
                val rep = k * k / dist
                val fx = ddx / dist * rep; val fy = ddy / dist * rep
                dx[i] += fx; dy[i] += fy; dx[j] -= fx; dy[j] -= fy
            }
            // 引力（沿边）
            for ((a, b) in pairs) {
                var ddx = px[a] - px[b]; var ddy = py[a] - py[b]
                var dist = sqrt(ddx * ddx + ddy * ddy)
                if (dist < 0.001f) dist = 0.001f
                val att = dist * dist / k
                val fx = ddx / dist * att; val fy = ddy / dist * att
                dx[a] -= fx; dy[a] -= fy; dx[b] += fx; dy[b] += fy
            }
            // 应用（限速 + 向心）
            for (i in 0 until n) {
                dx[i] -= px[i] * 0.05f; dy[i] -= py[i] * 0.05f
                val d = sqrt(dx[i] * dx[i] + dy[i] * dy[i])
                if (d > 0.0001f) {
                    val capped = min(d, temp)
                    px[i] += dx[i] / d * capped; py[i] += dy[i] / d * capped
                }
            }
            temp *= 0.97f
        }
        var maxR = 0.0001f
        for (i in 0 until n) {
            val r = sqrt(px[i] * px[i] + py[i] * py[i]); if (r > maxR) maxR = r
        }
        return ids.mapIndexed { i, id -> id to Pt(px[i] / maxR, py[i] / maxR) }.toMap()
    }
}
