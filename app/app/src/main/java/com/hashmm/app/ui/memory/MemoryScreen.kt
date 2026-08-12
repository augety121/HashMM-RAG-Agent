package com.hashmm.app.ui.memory

import androidx.compose.foundation.background
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.draw.clip
import androidx.compose.material3.Surface
import androidx.compose.material.icons.outlined.Search
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.sync.SyncRepository
import com.hashmm.app.data.sync.UserMemory
import com.hashmm.app.ui.components.HmmCard
import com.hashmm.app.ui.components.HmmPageScaffold
import com.hashmm.app.ui.components.HmmPullRefresh
import com.hashmm.app.ui.components.HmmSkeletonList
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import com.hashmm.app.ui.theme.AppSpacing
import com.hashmm.app.ui.theme.AppType
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class MemoryUiState(
    val loading: Boolean = false,
    val memories: List<UserMemory> = emptyList(),
    // V246 联邦召回（配套后端 V249 /api/memory/recall：长期记忆+经验回放+画像+图谱实体）
    val query: String = "",
    val searching: Boolean = false,
    val hits: List<com.hashmm.app.data.remote.MemoryHit> = emptyList(),
    val searchNote: String = "",
)

@HiltViewModel
class MemoryViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val hub: com.hashmm.app.data.remote.MemoryHubRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(MemoryUiState(loading = true))
    val ui: StateFlow<MemoryUiState> = _ui.asStateFlow()

    init { load() }

    fun load() {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true)
            val mem = sync.syncMemories()
            _ui.value = _ui.value.copy(loading = false, memories = mem)
        }
    }

    fun onQuery(q: String) { _ui.value = _ui.value.copy(query = q) }

    fun search() {
        val q = _ui.value.query.trim()
        if (q.isBlank()) { _ui.value = _ui.value.copy(hits = emptyList(), searchNote = ""); return }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(searching = true)
            val (hits, note) = hub.recall(q)
            _ui.value = _ui.value.copy(searching = false, hits = hits,
                searchNote = if (hits.isEmpty() && note.isBlank()) "没有命中的记忆" else note)
        }
    }

    fun clearSearch() { _ui.value = _ui.value.copy(query = "", hits = emptyList(), searchNote = "") }
}

@Composable
fun MemoryScreen(onBack: () -> Unit, viewModel: MemoryViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val grouped = ui.memories.groupBy { it.category.ifBlank { "其他" } }

    HmmPageScaffold(
        title = "记忆中心",
        subtitle = "偏好 · 经验 · 画像 · 联邦召回",
        onBack = onBack,
        actions = {
            IconButton(onClick = { viewModel.load() }) {
                Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            Column(Modifier.fillMaxSize()) {
            // ── V246 联邦记忆搜索：一条查询同时搜 长期记忆/经验回放/画像/图谱实体（后端 V249）──
            MemorySearchBar(
                query = ui.query, searching = ui.searching,
                onChange = viewModel::onQuery, onSearch = viewModel::search, onClear = viewModel::clearSearch,
            )
            if (ui.hits.isNotEmpty() || ui.searchNote.isNotBlank()) {
                MemoryHitsSection(hits = ui.hits, note = ui.searchNote)
            }
            Box(Modifier.fillMaxSize()) {
            when {
                ui.loading && ui.memories.isEmpty() ->
                    Column(Modifier.fillMaxSize().padding(horizontal = AppSpacing.page)) {
                        Spacer(Modifier.height(AppSpacing.sm))
                        HmmSkeletonList(count = 5)
                    }
                ui.memories.isEmpty() -> HmmStateView(
                    kind = HmmStateKind.Empty,
                    icon = Icons.Outlined.Memory,
                    title = "还没有记忆",
                    message = "在电脑端客户端配置好同步后，小哈记住的关于你的信息会显示在这里",
                )
                else -> HmmPullRefresh(refreshing = ui.loading, onRefresh = { viewModel.load() }) {
                    LazyColumn(
                        Modifier.fillMaxSize().padding(horizontal = AppSpacing.page),
                        verticalArrangement = Arrangement.spacedBy(AppSpacing.sm),
                    ) {
                        item { Spacer(Modifier.height(AppSpacing.xs)) }
                        grouped.forEach { (category, items) ->
                            item {
                                // V244：分类标题改灰色小字分区标签（全 App 同规）
                                Text(
                                    category,
                                    fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.5.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(start = 2.dp, top = AppSpacing.sm, bottom = 2.dp),
                                )
                            }
                            items(items, key = { it.id }) { mem -> MemoryCard(mem) }
                        }
                        item { Spacer(Modifier.height(AppSpacing.md)) }
                    }
                }
            }
            }
            }
        }
    }
}

/** V246 联邦记忆搜索条：白色圆角 + 放大镜 + 品牌光标（与知识图谱搜索条同语言）。 */
@Composable
private fun MemorySearchBar(
    query: String, searching: Boolean,
    onChange: (String) -> Unit, onSearch: () -> Unit, onClear: () -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surface, shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = AppSpacing.page)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 13.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Search, contentDescription = null,
                tint = cs.onSurfaceVariant, modifier = Modifier.size(19.dp))
            Spacer(Modifier.width(10.dp))
            Box(Modifier.weight(1f)) {
                if (query.isEmpty()) Text("搜全部记忆（长期·经验·画像·图谱）",
                    fontSize = 13.5.sp, color = cs.onSurfaceVariant.copy(alpha = 0.8f))
                BasicTextField(
                    value = query, onValueChange = onChange, singleLine = true,
                    textStyle = TextStyle(fontSize = 13.5.sp, color = cs.onSurface),
                    cursorBrush = SolidColor(cs.onSurface),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { onSearch() }),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            if (searching) {
                androidx.compose.material3.CircularProgressIndicator(
                    Modifier.size(15.dp), strokeWidth = 1.8.dp, color = cs.onSurfaceVariant)
            } else if (query.isNotEmpty()) {
                Text("清除", fontSize = 12.sp, color = cs.onSurfaceVariant,
                    modifier = Modifier.clip(RoundedCornerShape(50)).clickable(onClick = onClear)
                        .padding(horizontal = 6.dp, vertical = 3.dp))
                Spacer(Modifier.width(4.dp))
                Surface(color = cs.primary, shape = RoundedCornerShape(50),
                    modifier = Modifier.clip(RoundedCornerShape(50)).clickable(onClick = onSearch)) {
                    Text("召回", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = cs.onPrimary,
                        modifier = Modifier.padding(horizontal = 13.dp, vertical = 6.dp))
                }
            }
        }
    }
}

/** V246 召回结果：一张分组白卡，来源徽章（长期记忆米色 / 其余灰）+ 文本 + 相对时间。 */
@Composable
private fun MemoryHitsSection(hits: List<com.hashmm.app.data.remote.MemoryHit>, note: String) {
    val cs = MaterialTheme.colorScheme
    Column(Modifier.padding(horizontal = AppSpacing.page)) {
        Spacer(Modifier.height(10.dp))
        Text("召回结果 ${hits.size}", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.5.sp, color = cs.onSurfaceVariant,
            modifier = Modifier.padding(start = 2.dp, bottom = 8.dp))
        if (note.isNotBlank() && hits.isEmpty()) {
            Text(note, fontSize = 12.5.sp, color = cs.onSurfaceVariant,
                modifier = Modifier.padding(start = 2.dp, bottom = 6.dp))
        }
        if (hits.isNotEmpty()) Surface(color = cs.surface, shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth()) {
            Column {
                hits.take(12).forEachIndexed { i, h ->
                    if (i > 0) Box(Modifier.fillMaxWidth().padding(start = 15.dp).height(0.5.dp)
                        .background(cs.outlineVariant.copy(alpha = 0.6f)))
                    Row(Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 11.dp),
                        verticalAlignment = Alignment.Top) {
                        val beige = h.kind == "service"
                        Surface(
                            color = if (beige) com.hashmm.app.ui.theme.WarmBeige else cs.surfaceVariant,
                            shape = RoundedCornerShape(7.dp),
                        ) {
                            Text(h.source, fontSize = 10.sp, fontWeight = FontWeight.Bold,
                                color = if (beige) com.hashmm.app.ui.theme.OnWarmBeige else cs.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp))
                        }
                        Spacer(Modifier.width(10.dp))
                        Text(h.text, fontSize = 13.sp, color = cs.onSurface, lineHeight = 19.sp,
                            modifier = Modifier.weight(1f))
                    }
                }
            }
        }
        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun MemoryCard(mem: UserMemory) {
    val cs = MaterialTheme.colorScheme
    HmmCard {
        if (mem.key.isNotBlank()) {
            Text(mem.key, style = AppType.subtitle, color = cs.onSurface, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(3.dp))
        }
        Text(mem.value, style = AppType.body, color = cs.onSurface)
        if (mem.confidence > 0.0) {
            Spacer(Modifier.height(AppSpacing.sm))
            Row(verticalAlignment = Alignment.CenterVertically) {
                // 置信度条：设计系统里没有对应组件，作为记忆卡专属细节保留（用令牌尺寸）
                Box(
                    Modifier.width(60.dp).height(4.dp)
                        .background(cs.outlineVariant.copy(alpha = 0.5f), RoundedCornerShape(2.dp)),
                ) {
                    Box(
                        Modifier.fillMaxWidth(mem.confidence.toFloat().coerceIn(0f, 1f)).height(4.dp)
                            .background(cs.primary, RoundedCornerShape(2.dp)),
                    )
                }
                Spacer(Modifier.width(AppSpacing.sm))
                Text("${(mem.confidence * 100).toInt()}%", style = AppType.label, color = cs.onSurfaceVariant)
            }
        }
    }
}
