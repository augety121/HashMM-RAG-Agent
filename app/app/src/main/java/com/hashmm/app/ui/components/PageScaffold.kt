package com.hashmm.app.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.pulltorefresh.rememberPullToRefreshState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.layout.RowScope
import com.hashmm.app.ui.theme.AppSpacing

/**
 * 页面骨架 + 下拉刷新（V300 第一期）—— 全 App 页面的统一外壳。
 *
 * HmmPageScaffold：页眉（复用 ScreenHeader）+ 内容区，背景色统一，页面横向边距统一。
 * HmmPullRefresh：下拉刷新容器（Material3 官方 PullToRefreshBox），大厂 App 每个可刷新列表都有。
 *
 * 用法：
 *   HmmPageScaffold("记忆中心", onBack) { padding ->
 *       HmmPullRefresh(refreshing = ui.loading, onRefresh = vm::refresh) {
 *           ... 内容（LazyColumn / Column）...
 *       }
 *   }
 */

@Composable
fun HmmPageScaffold(
    title: String,
    subtitle: String = "",
    onBack: () -> Unit,
    actions: @Composable RowScope.() -> Unit = {},
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        topBar = { ScreenHeader(title = title, subtitle = subtitle, onBack = onBack, actions = actions) },
        containerColor = MaterialTheme.colorScheme.background,
        content = content,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HmmPullRefresh(
    refreshing: Boolean,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    val state = rememberPullToRefreshState()
    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = onRefresh,
        state = state,
        modifier = modifier.fillMaxSize(),
    ) { content() }
}

/**
 * 可滚动内容列（页面主体常用）。统一横向 page 边距 + 顶/底留白，内容按令牌间距排布。
 * 简单页面直接用它包内容；需要虚拟化的长列表用 LazyColumn（同样引用 AppSpacing）。
 */
@Composable
fun HmmScrollContent(
    padding: PaddingValues,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Column(
        modifier
            .fillMaxSize()
            .padding(padding)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = AppSpacing.page, vertical = AppSpacing.sm),
    ) { content() }
}
