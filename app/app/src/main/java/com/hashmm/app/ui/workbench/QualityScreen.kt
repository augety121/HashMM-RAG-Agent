package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.BarChart
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.FactCheck
import androidx.compose.material.icons.outlined.HourglassEmpty
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.hashmm.app.data.remote.QualityData
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** V221 质量看板（Kit 重铸）：核心三格 + 中文指标行（英文原名作副题溯源）。
 *  字段映射对齐后端 quality_monitor.dashboard；未知字段回退原名，绝不造数。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QualityScreen(
    onBack: () -> Unit,
    onOpenRuns: () -> Unit = {},
    viewModel: AdminToolsViewModel = hiltViewModel(),
) {
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf(WorkbenchCache.quality) }   // V275 跨导航持久化：先显上次
    var refreshing by remember { mutableStateOf(false) }
    fun reload() { scope.launch { refreshing = true; data = viewModel.quality().also { WorkbenchCache.quality = it }; refreshing = false } }
    LaunchedEffect(Unit) { if (WorkbenchCache.quality == null) reload() }   // V275 有缓存不空转重拉
    val d = data
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        ModuleHeader(Icons.Outlined.BarChart, "质量看板", "近 7 天关键指标（与电脑端同源）", onBack)
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                d == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载中")
                d.error != null -> HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = d.error!!, onRetry = { reload() })
                else -> {
                    fun pct(v: Double?): String = v?.let { String.format("%.0f%%", it * 100) } ?: "不可评估"
                    Column(Modifier.fillMaxSize()) {
                        StatTriple(
                            "抽检样本" to d.samples.toString(),
                            "平均有据率" to pct(d.avgGroundedRatio),
                            "工具执行率" to pct(d.toolQuality.executionSuccessRate),
                            toneB = if (d.avgGroundedRatio == null) KitTone.Warn else KitTone.Success,
                        )
                        Spacer(Modifier.height(10.dp))
                        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                            item {
                                val verdict = qualityVerdict(d)
                                ModuleInsight(
                                    icon = when (verdict.code) {
                                        "healthy" -> Icons.Outlined.CheckCircle
                                        "review" -> Icons.Outlined.FactCheck
                                        else -> Icons.Outlined.HourglassEmpty
                                    },
                                    title = verdict.title,
                                    body = verdict.body,
                                    tone = verdict.tone,
                                    badge = verdict.badge,
                                    actionLabel = if (d.samples > 0) "查看运行轨迹" else "",
                                    onAction = if (d.samples > 0) onOpenRuns else null,
                                )
                                Spacer(Modifier.height(12.dp))
                            }
                            if (d.samples == 0) item {
                                ModuleSectionLabel("采样状态", "没有数据不等于质量为零")
                                KitGroup {
                                    MetricRow(
                                        "线上抽检已就绪",
                                        "每 ${d.sampleRate.coerceAtLeast(1)} 轮抽 1 轮",
                                        "完成足够数量的 Chat 后才会产生样本；页面不会用演示数据填充",
                                        KitTone.Warn,
                                    )
                                }
                                Spacer(Modifier.height(12.dp))
                            }
                            item {
                                ModuleSectionLabel("质量指标", "口径来自后端真实流量采样")
                                KitGroup {
                                    MetricRow("可评估样本", d.evaluableSamples.toString(), "有引用证据、可以核验接地率")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("不可评估样本", d.notEvaluableSamples.toString(), "没有足够证据时单独计数，不当作满分或零分", if (d.notEvaluableSamples > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("平均来源", String.format("%.2f", d.avgSources), "每个抽检回答的检索来源数")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("平均引用", String.format("%.2f", d.avgCitations), "回答正文中的可解析引用数")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("平均耗时", "${d.avgLatencyMs} ms", "从请求到完整回答")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("无来源回答", d.answersWithoutSources.toString(), "需要检索但没有来源时应重点检查", if (d.answersWithoutSources > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("弱依据占比", pct(d.weakRate), "仅以可评估样本为分母", if (d.weakRate > 0) KitTone.Warn else KitTone.Success)
                                }
                            }
                            item {
                                Spacer(Modifier.height(12.dp))
                                ModuleSectionLabel("证据门控完成", "必需契约、执行轨迹和用户验收必须真正闭环")
                                val cq = d.completionGateQuality
                                KitGroup {
                                    MetricRow("带完成门运行", cq.gateRuns.toString(), "Chat、长任务和多 Agent 共享的完成判定协议")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("可验证闭环率", pct(cq.verifiedRate), "只有全部必需条件有运行证据时才进入分子")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("带限制交付", cq.deliveredWithLimitsRuns.toString(), "已有结果但仍需用户或独立来源复核",
                                        if (cq.deliveredWithLimitsRuns > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("未闭环或阻塞", (cq.incompleteRuns + cq.blockedRuns).toString(), "仍有失败条件、可继续动作或权限阻塞",
                                        if (cq.incompleteRuns + cq.blockedRuns > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("重复工具循环", cq.repeatedToolCallRuns.toString(), "相同工具和参数重复三次以上的运行",
                                        if (cq.repeatedToolCallRuns > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("不安全完成声明", cq.unsafeCompletionClaims.toString(), "声称完成但门禁条件不成立；应始终为 0",
                                        if (cq.unsafeCompletionClaims > 0) KitTone.Warn else KitTone.Success)
                                }
                                if (cq.scope.isNotBlank()) {
                                    Spacer(Modifier.height(7.dp))
                                    Text(cq.scope, fontSize = 10.5.sp, lineHeight = 16.sp,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            item {
                                Spacer(Modifier.height(12.dp))
                                ModuleSectionLabel("任务闭环健康", "证据图来自 Chat、持续任务和协作任务的同一运行协议")
                                val gq = d.taskGraphQuality
                                KitGroup {
                                    MetricRow("带任务图运行", gq.graphRuns.toString(), "已连接目标、证据、工具、成员、产物和检查的运行")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("追踪覆盖", pct(gq.traceCoverage), "运行清单中包含任务证据图的比例",
                                        if (gq.traceCoverage != null && gq.traceCoverage < 0.9) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("未闭环运行", gq.blockedRuns.toString(), "仍有确定性检查、失败工具或缺失交付",
                                        if (gq.blockedRuns > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("开放阻塞", gq.openBlockers.toString(), "需要下一轮或人工处理的真实阻塞项",
                                        if (gq.openBlockers > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("主张证据连接", pct(gq.claimEvidenceCoverage), "只统计可评估的事实主张运行")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("图完整性异常", gq.integrityViolations.toString(), "必须由运行事实构建，不能包含模型推断边或所有者数据",
                                        if (gq.integrityViolations > 0) KitTone.Warn else KitTone.Success)
                                }
                                if (gq.scope.isNotBlank()) {
                                    Spacer(Modifier.height(7.dp))
                                    Text(gq.scope, fontSize = 10.5.sp, lineHeight = 16.sp,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            item {
                                Spacer(Modifier.height(12.dp))
                                ModuleSectionLabel("执行前沿健康", "图驱动的下一工作集，只使用真实工具注册表与持久权限范围")
                                val fq = d.executionFrontierQuality
                                KitGroup {
                                    MetricRow("带工作集运行", fq.frontierRuns.toString(), "已从任务阻塞计算最小可行路线的运行")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("路线覆盖", pct(fq.routeCoverage), "开放工作项中存在当前范围内可行路线的比例")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("开放工作项", fq.unresolvedItems.toString(), "仍需补证据、交付、恢复工具或复核的项目",
                                        if (fq.unresolvedItems > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("当前可行路线", fq.readyRoutes.toString(), "能力存在且位于持久执行范围内；仍未代表已批准或执行")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("权限受限项", fq.scopeBlockedItems.toString(), "首选工具未进入当前范围或网络策略不允许",
                                        if (fq.scopeBlockedItems > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("前沿完整性异常", fq.integrityViolations.toString(), "不能自动执行、扩大范围或让模型选择服务端路线",
                                        if (fq.integrityViolations > 0) KitTone.Warn else KitTone.Success)
                                }
                                if (fq.scope.isNotBlank()) {
                                    Spacer(Modifier.height(7.dp))
                                    Text(fq.scope, fontSize = 10.5.sp, lineHeight = 16.sp,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            item {
                                Spacer(Modifier.height(12.dp))
                                ModuleSectionLabel("工具执行健康", "来自服务端运行清单，不把返回成功冒充工具选择正确")
                                val tq = d.toolQuality
                                KitGroup {
                                    MetricRow("运行清单", tq.runManifests.toString(), "带确定性完成检查的 Chat 运行")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("可评估工具运行", tq.evaluableRuns.toString(), "本轮实际执行了工具并留下成功或失败状态")
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("执行成功率", pct(tq.executionSuccessRate), "只衡量工具是否返回成功，不代表工具名与参数正确",
                                        if (tq.executionSuccessRate != null && tq.executionSuccessRate < 0.95) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("执行失败", "${tq.failedToolCalls}/${tq.toolCalls}", "失败工具调用数 / 全部有状态调用数",
                                        if (tq.failedToolCalls > 0) KitTone.Warn else KitTone.Success)
                                    KitInsetDivider(start = 15.dp)
                                    MetricRow("人工标记工具错误", tq.wrongToolFeedback.toString(), "来自结构化 Chat 负反馈，仍需管理员复核",
                                        if (tq.wrongToolFeedback > 0) KitTone.Warn else KitTone.Success)
                                    if (tq.truncated) {
                                        KitInsetDivider(start = 15.dp)
                                        MetricRow("统计范围", "最近 10000 条", "达到运行清单保护上限", KitTone.Warn)
                                    }
                                }
                                if (tq.scope.isNotBlank()) {
                                    Spacer(Modifier.height(7.dp))
                                    Text(tq.scope, fontSize = 10.5.sp, lineHeight = 16.sp,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            if (d.daily.isNotEmpty()) item {
                                Spacer(Modifier.height(12.dp))
                                ModuleSectionLabel("每日趋势", "样本量与平均有据率")
                                val fmt = SimpleDateFormat("MM-dd", Locale.getDefault())
                                KitGroup {
                                    d.daily.forEachIndexed { index, day ->
                                        if (index > 0) KitInsetDivider(start = 15.dp)
                                        QualityTrendRow(
                                            day = fmt.format(Date(day.epochDay * 86_400_000L)),
                                            samples = day.samples,
                                            ratio = day.groundedRatio,
                                        )
                                    }
                                }
                            }
                            item { Spacer(Modifier.height(12.dp)) }
                        }
                    }
                }
            }
        }
    }
}

internal data class QualityVerdict(
    val code: String,
    val title: String,
    val body: String,
    val badge: String,
    val tone: KitTone,
)

internal fun qualityVerdict(data: QualityData): QualityVerdict = when {
    data.completionGateQuality.unsafeCompletionClaims > 0 || data.completionGateQuality.integrityViolations > 0 -> QualityVerdict(
        code = "review",
        title = "完成门完整性需要立即复核",
        body = "检测到 ${data.completionGateQuality.unsafeCompletionClaims} 条不安全完成声明和 ${data.completionGateQuality.integrityViolations} 条门禁完整性异常。模型评分不能替代运行证据或用户验收。",
        badge = "完成门异常",
        tone = KitTone.Warn,
    )
    data.completionGateQuality.incompleteRuns + data.completionGateQuality.blockedRuns > 0 -> QualityVerdict(
        code = "review",
        title = "仍有任务没有通过完成门",
        body = "未闭环 ${data.completionGateQuality.incompleteRuns} 条，受阻 ${data.completionGateQuality.blockedRuns} 条。请按任务的最小可行动作继续，不要只重试最终回答。",
        badge = "门禁复核",
        tone = KitTone.Warn,
    )
    data.executionFrontierQuality.integrityViolations > 0 -> QualityVerdict(
        code = "review",
        title = "执行前沿完整性需要立即复核",
        body = "检测到 ${data.executionFrontierQuality.integrityViolations} 条执行前沿违反确定性构建约束。前沿不能自动执行、扩大权限或让模型替代服务端选择路线。",
        badge = "前沿异常",
        tone = KitTone.Warn,
    )
    data.executionFrontierQuality.scopeBlockedItems > 0 -> QualityVerdict(
        code = "review",
        title = "部分下一步受当前权限范围限制",
        body = "有 ${data.executionFrontierQuality.scopeBlockedItems} 个工作项的首选工具不在持久范围内。请在对应任务中核对所需能力，不要通过全局放宽权限来绕过审批。",
        badge = "范围受限",
        tone = KitTone.Warn,
    )
    data.taskGraphQuality.integrityViolations > 0 -> QualityVerdict(
        code = "review",
        title = "任务图完整性需要立即复核",
        body = "检测到 ${data.taskGraphQuality.integrityViolations} 条不符合确定性构建约束的运行图。请检查运行清单生成链，不能让模型推断边或所有者数据进入图。",
        badge = "完整性异常",
        tone = KitTone.Warn,
    )
    data.taskGraphQuality.openBlockers > 0 -> QualityVerdict(
        code = "review",
        title = "仍有任务没有真正闭环",
        body = "${data.taskGraphQuality.blockedRuns} 个运行共有 ${data.taskGraphQuality.openBlockers} 个开放阻塞。请下钻运行轨迹，优先处理失败工具、缺失产物和未通过检查。",
        badge = "闭环复核",
        tone = KitTone.Warn,
    )
    data.toolQuality.failedRuns > 0 || data.toolQuality.wrongToolFeedback > 0 -> QualityVerdict(
        code = "review",
        title = "发现工具链需要复核",
        body = "工具执行失败运行 ${data.toolQuality.failedRuns} 条，人工标记工具错误 ${data.toolQuality.wrongToolFeedback} 条。执行成功不代表工具选择正确，请结合严格工具评测和运行轨迹定位。",
        badge = "工具复核",
        tone = KitTone.Warn,
    )
    data.samples == 0 && data.toolQuality.runManifests == 0 -> QualityVerdict(
        code = "waiting",
        title = "质量采样已就绪",
        body = "当前还没有线上抽检样本。系统会保留不可评估状态，不用演示分数填充页面。",
        badge = "等待样本",
        tone = KitTone.Neutral,
    )
    data.samples > 0 && data.avgGroundedRatio == null -> QualityVerdict(
        code = "waiting",
        title = "当前样本不可评估",
        body = "已有 ${data.samples} 个样本，但缺少足够的引用证据，不能给出有据率结论。",
        badge = "证据不足",
        tone = KitTone.Warn,
    )
    data.answersWithoutSources > 0 || data.weaklyGrounded > 0 -> QualityVerdict(
        code = "review",
        title = "发现需要复核的回答",
        body = "无来源回答 ${data.answersWithoutSources} 条，弱依据回答 ${data.weaklyGrounded} 条。请下钻运行轨迹检查原对话和失败项。",
        badge = "需要复核",
        tone = KitTone.Warn,
    )
    else -> QualityVerdict(
        code = "healthy",
        title = "当前可评估样本未发现红线",
        body = "已检查 ${data.evaluableSamples} 个 RAG 样本和 ${data.toolQuality.evaluableRuns} 个工具运行；这只是当前可观察状态，不代表所有回答或工具选择都已验证。",
        badge = "当前样本",
        tone = KitTone.Success,
    )
}

@Composable
private fun QualityTrendRow(day: String, samples: Int, ratio: Double?) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(day, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.width(48.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("$samples 个样本", fontSize = 10.5.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                Text(ratio?.let { String.format("%.0f%%", it * 100) } ?: "不可评估",
                    fontSize = 11.sp, fontWeight = FontWeight.SemiBold,
                    color = if (ratio == null) MaterialTheme.colorScheme.onSurfaceVariant
                            else MaterialTheme.colorScheme.onSurface)
            }
            Spacer(Modifier.height(6.dp))
            LinearProgressIndicator(
                progress = { ratio?.coerceIn(0.0, 1.0)?.toFloat() ?: 0f },
                modifier = Modifier.fillMaxWidth().height(5.dp),
                color = if (ratio == null) MaterialTheme.colorScheme.outlineVariant
                        else MaterialTheme.colorScheme.primary,
                trackColor = MaterialTheme.colorScheme.surfaceVariant,
            )
        }
    }
}

/** 指标中文映射（对齐 quality_monitor.dashboard 字段；未知回退原名）。 */
internal fun qualityLabel(k: String): String = when (k) {
    "days" -> "统计天数"
    "samples" -> "抽检样本"
    "avg_grounded_ratio" -> "平均有据率"
    "avg_sources" -> "平均引用源数"
    "avg_citations" -> "平均引用条数"
    "avg_latency_ms" -> "平均响应耗时"
    "answers_without_sources" -> "无来源回答数"
    "weakly_grounded" -> "弱依据回答数"
    "weak_rate" -> "弱依据占比"
    "sample_rate" -> "抽检比例"
    else -> k
}
