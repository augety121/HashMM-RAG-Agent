package com.hashmm.app.ui.usage

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.BarChart
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Payments
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.components.HmmBadge
import com.hashmm.app.ui.components.HmmCard
import com.hashmm.app.ui.components.HmmCardHeader
import com.hashmm.app.ui.components.HmmChip
import com.hashmm.app.ui.components.HmmPageScaffold
import com.hashmm.app.ui.components.HmmSectionTitle
import com.hashmm.app.ui.components.HmmSkeletonCard
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import com.hashmm.app.ui.components.HmmStatCard
import com.hashmm.app.ui.components.HmmTone
import com.hashmm.app.ui.theme.AppSpacing

@Composable
fun UsageScreen(onBack: () -> Unit, viewModel: UsageViewModel = hiltViewModel()) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    HmmPageScaffold(
        title = "使用概览",
        subtitle = "了解 HashMM 为你和团队完成了多少工作",
        onBack = onBack,
        actions = {
            IconButton(onClick = { viewModel.load(ui.days) }) {
                Icon(Icons.Outlined.Refresh, contentDescription = "刷新")
            }
        },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState())
                .padding(horizontal = AppSpacing.page, vertical = AppSpacing.md),
            verticalArrangement = Arrangement.spacedBy(AppSpacing.md),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(AppSpacing.sm)) {
                listOf(7, 30, 90).forEach { d ->
                    HmmChip("近 $d 天", onClick = { viewModel.load(d) }, selected = ui.days == d)
                }
            }

            val s = ui.stat
            when {
                ui.loading && s == null -> repeat(3) { HmmSkeletonCard(lines = 2) }
                s?.error != null -> HmmStateView(
                    kind = HmmStateKind.Error,
                    icon = Icons.Outlined.BarChart,
                    title = "暂时无法读取使用情况",
                    message = s.error,
                    onRetry = { viewModel.load(ui.days) },
                )
                s == null -> Unit
                else -> {
                    HmmCard {
                        HmmCardHeader(
                            title = if (s.scope == "team") "团队使用情况" else "我的使用情况",
                            sub = "统计来自真实对话和任务，不包含演示数据",
                            icon = if (s.scope == "team") Icons.Outlined.Groups else Icons.Outlined.AutoAwesome,
                            right = { HmmBadge(if (s.scope == "team") "整个团队" else "仅自己", HmmTone.Accent) },
                        )
                        Spacer(Modifier.height(AppSpacing.lg))
                        Text(
                            if (s.requests > 0) "已完成 ${s.requests} 次模型调用" else "这段时间还没有产生新的模型调用",
                            style = MaterialTheme.typography.titleLarge,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                        Spacer(Modifier.height(AppSpacing.xs))
                        Text(
                            if (s.requests > 0) "共处理 ${formatTokens(s.tokens)} 内容单元，估算花费 ${formatMoney(s.cost, s.currency)}"
                            else "开始一次对话、资料整理或长任务后，这里会自动出现真实记录。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(AppSpacing.md)) {
                        HmmStatCard("已完成", s.requests.toString(), Modifier.weight(1f), "次调用", Icons.Outlined.AutoAwesome, HmmTone.Accent)
                        HmmStatCard("处理量", formatTokens(s.tokens), Modifier.weight(1f), "内容单元", Icons.Outlined.BarChart)
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(AppSpacing.md)) {
                        HmmStatCard("发送给模型", formatTokens(s.tokensIn), Modifier.weight(1f), icon = Icons.Outlined.BarChart)
                        HmmStatCard("模型生成", formatTokens(s.tokensOut), Modifier.weight(1f), icon = Icons.Outlined.BarChart)
                    }
                    HmmStatCard("估算花费", formatMoney(s.cost, s.currency), unit = "按当前模型单价估算", icon = Icons.Outlined.Payments)

                    if (s.byMember.isNotEmpty()) {
                        HmmSectionTitle("团队成员")
                        s.byMember.take(8).forEach { member ->
                            HmmCard {
                                HmmCardHeader(member.username, "${member.requests} 次工作 · ${formatTokens(member.tokens)}", Icons.Outlined.Groups) {
                                    Text(formatMoney(member.cost, s.currency), style = MaterialTheme.typography.labelMedium)
                                }
                            }
                        }
                    }
                    if (s.byModel.isNotEmpty()) {
                        HmmSectionTitle("使用的模型")
                        s.byModel.forEach { model ->
                            HmmCard {
                                HmmCardHeader(model.model, "${model.requests} 次调用 · ${formatTokens(model.tokens)}", Icons.Outlined.AutoAwesome) {
                                    Text(formatMoney(model.cost, s.currency), style = MaterialTheme.typography.labelMedium)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun formatTokens(t: Long): String = when {
    t >= 1_000_000 -> String.format("%.2fM", t / 1_000_000.0)
    t >= 1_000 -> String.format("%.1fK", t / 1_000.0)
    else -> t.toString()
}

private fun formatMoney(value: Double, currency: String): String =
    "${if (currency == "CNY") "¥" else "$"}${String.format("%.4f", value)}"
