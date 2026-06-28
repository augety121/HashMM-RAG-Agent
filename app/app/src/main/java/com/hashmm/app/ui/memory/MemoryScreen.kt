package com.hashmm.app.ui.memory
import com.hashmm.app.ui.components.ScreenHeader

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.sync.SyncRepository
import com.hashmm.app.data.sync.UserMemory
import com.hashmm.app.ui.components.HashMascot
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class MemoryUiState(
    val loading: Boolean = false,
    val memories: List<UserMemory> = emptyList(),
)

@HiltViewModel
class MemoryViewModel @Inject constructor(
    private val sync: SyncRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(MemoryUiState(loading = true))
    val ui: StateFlow<MemoryUiState> = _ui.asStateFlow()

    init { load() }

    fun load() {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true)
            val mem = sync.syncMemories()
            _ui.value = MemoryUiState(loading = false, memories = mem)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MemoryScreen(onBack: () -> Unit, viewModel: MemoryViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val grouped = ui.memories.groupBy { it.category.ifBlank { "其他" } }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            ScreenHeader(title = "记忆中心", onBack = onBack) {
                IconButton(onClick = { viewModel.load() }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
                    }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                ui.loading && ui.memories.isEmpty() -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.memories.isEmpty() -> Column(
                    Modifier.align(Alignment.Center).padding(40.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    HashMascot(Modifier.size(96.dp))
                    Spacer(Modifier.height(18.dp))
                    Text("还没有记忆", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "在电脑端客户端配置好同步后，小哈记住的关于你的信息会显示在这里",
                        fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
                    )
                }
                else -> LazyColumn(
                    Modifier.fillMaxSize().padding(horizontal = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    item { Spacer(Modifier.height(4.dp)) }
                    grouped.forEach { (category, items) ->
                        item {
                            Text(
                                category,
                                fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(top = 8.dp, bottom = 2.dp),
                            )
                        }
                        items(items, key = { it.id }) { mem -> MemoryCard(mem) }
                    }
                    item { Spacer(Modifier.height(12.dp)) }
                }
            }
        }
    }
}

@Composable
private fun MemoryCard(mem: UserMemory) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.25f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 13.dp)) {
            if (mem.key.isNotBlank()) {
                Text(mem.key, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                Spacer(Modifier.height(3.dp))
            }
            Text(mem.value, fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurface)
            if (mem.confidence > 0.0) {
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.width(60.dp).height(4.dp)
                            .background(MaterialTheme.colorScheme.outline.copy(alpha = 0.25f), RoundedCornerShape(2.dp)),
                    ) {
                        Box(
                            Modifier.fillMaxWidth(mem.confidence.toFloat().coerceIn(0f, 1f)).height(4.dp)
                                .background(MaterialTheme.colorScheme.primary, RoundedCornerShape(2.dp)),
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                    Text("${(mem.confidence * 100).toInt()}%", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}
