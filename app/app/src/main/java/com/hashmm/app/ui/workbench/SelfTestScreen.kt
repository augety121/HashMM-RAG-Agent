package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.outlined.NetworkCheck
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.hashmm.app.data.remote.AdminToolsRepository
import com.hashmm.app.ui.components.HmmStateKind
import com.hashmm.app.ui.components.HmmStateView
import kotlinx.coroutines.launch

/** V272 测试中枢——**跑 App 自己的原生测试**（不再调后端桌面测试）：
 *  分组勾选（Markdown 渲染 / 消息列表重构 / 时间显示与排序 / 后台直播 / 缓存 + 压力测试）→
 *  一键在设备本地运行 → 逐项 通过/失败/跳过 + 用时 + 详细日志。压力项默认不勾。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SelfTestScreen(onBack: () -> Unit, viewModel: AdminToolsViewModel = hiltViewModel()) {
    val scope = rememberCoroutineScope()
    var suites by remember { mutableStateOf<List<AdminToolsRepository.SelfTestSuite>?>(null) }
    var loadErr by remember { mutableStateOf<String?>(null) }
    // V273 跨导航持久化：勾选与上次结果从 NativeTestHub 恢复——退出测试页再进来仍在，不再一进去空。
    var sel by remember { mutableStateOf(NativeTestHub.lastSel) }
    var run by remember { mutableStateOf(NativeTestHub.lastRun) }
    var running by remember { mutableStateOf(false) }
    var refreshing by remember { mutableStateOf(false) }
    var savedHint by remember { mutableStateOf(NativeTestHub.lastSavedPath) }

    fun reload() {
        scope.launch {
            refreshing = true
            // V272：测试中枢改跑 **App 原生测试**（测 App 自己的 Markdown 渲染/消息重构/时间排序/
            // 后台流/缓存 + 压力测试），不再调后端那套桌面测试。全部在设备本地跑，秒出、不依赖后端。
            val list = NativeTestEngine.suiteList()
            suites = list; loadErr = null
            if (sel.isEmpty()) sel = list.filter { !it.slow }.map { it.id }.toSet()   // 默认勾非压力项
            refreshing = false
        }
    }
    LaunchedEffect(Unit) { reload() }

    fun runIds(ids: Set<String>) {
        if (running || ids.isEmpty()) return
        scope.launch {
            running = true; run = null; savedHint = ""
            val result = NativeTestEngine.run(ids.toList())
            run = result
            // V273 持久化：记到 Hub（退出重进还在）+ 报告发后端落盘 data/selftest_reports（与桌面端一致）
            NativeTestHub.lastRun = result; NativeTestHub.lastSel = ids
            running = false
            if (result.error == null && result.results.isNotEmpty()) {
                val md = NativeTestEngine.buildReportMd(result)
                val (okSave, pathOrErr) = viewModel.saveSelftestReport(md)
                savedHint = if (okSave) "报告已存服务器：${pathOrErr}" else "报告未存服务器：${pathOrErr}"
                NativeTestHub.lastSavedPath = savedHint
            }
        }
    }

    fun doRun() = runIds(sel)

    val list = suites
    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        ModuleHeader(Icons.Outlined.NetworkCheck, "测试中枢", "勾选功能 · 一键跑 App 原生测试（含压力测试）", onBack)
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { reload() }, modifier = Modifier.fillMaxSize()) {
            when {
                list == null -> HmmStateView(kind = HmmStateKind.Loading, title = "加载套件清单")
                loadErr != null -> HmmStateView(kind = HmmStateKind.Error, title = "加载失败", message = loadErr!!, onRetry = { reload() })
                list.isEmpty() -> HmmStateView(kind = HmmStateKind.Empty, icon = Icons.Outlined.NetworkCheck,
                    title = "暂无测试项", message = "App 原生测试引擎未注册套件")
                else -> {
                    val groups = list.groupBy { it.group }
                    val r = run
                    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                        // 结果摘要（跑过才有）
                        if (r != null && r.error == null) {
                            item {
                                Spacer(Modifier.height(10.dp))
                                StatTriple("通过" to r.passCnt.toString(), "失败" to r.failCnt.toString(),
                                    "跳过" to r.skipCnt.toString(),
                                    toneB = if (r.failCnt > 0) KitTone.Error else KitTone.Success)
                            }
                        }
                        if (r != null && r.error != null) {
                            item {
                                Spacer(Modifier.height(10.dp))
                                KitGroup { MetricRow(label = "运行失败", value = r.error!!, tone = KitTone.Error) }
                            }
                        }
                        // 运行按钮
                        item {
                            Spacer(Modifier.height(10.dp))
                            ModuleSectionLabel("测试计划", "选择套件后运行，报告保留到服务器")
                            Button(onClick = { doRun() }, enabled = !running && sel.isNotEmpty(),
                                modifier = Modifier.fillMaxWidth()) {
                                if (running) {
                                    CircularProgressIndicator(Modifier.width(18.dp).height(18.dp), strokeWidth = 2.dp)
                                    Spacer(Modifier.width(8.dp))
                                    Text("测试中…")
                                } else {
                                    Text("运行选中的 ${sel.size} 项")
                                }
                            }
                            // V273 重新测试 / 只重跑失败（跑过且没在跑时才显示）
                            val rr = run
                            if (!running && rr != null && rr.results.isNotEmpty()) {
                                Spacer(Modifier.height(8.dp))
                                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    OutlinedButton(onClick = { runIds(sel) }, modifier = Modifier.weight(1f)) {
                                        Text("重新测试")
                                    }
                                    val failIds = rr.results.filter { !it.ok && !it.skip }.map { it.id }.toSet()
                                    if (failIds.isNotEmpty()) {
                                        OutlinedButton(onClick = { sel = failIds; runIds(failIds) }, modifier = Modifier.weight(1f)) {
                                            Text("只重跑失败(${failIds.size})")
                                        }
                                    }
                                }
                            }
                            // V273 报告落盘提示（发到服务器 data/selftest_reports）
                            if (!running && savedHint.isNotBlank()) {
                                Spacer(Modifier.height(6.dp))
                                Text(savedHint, style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        // 分组勾选 + 每项结果
                        groups.forEach { (group, items) ->
                            item {
                                Spacer(Modifier.height(12.dp))
                                Row(Modifier.fillMaxWidth().clickable {
                                    val ids = items.map { it.id }
                                    sel = if (ids.all { it in sel }) sel - ids.toSet() else sel + ids
                                }, verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.SpaceBetween) {
                                    Text(group, style = MaterialTheme.typography.titleSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.padding(start = 4.dp, bottom = 6.dp))
                                    Text(if (items.all { it.id in sel }) "取消全组" else "全组",
                                        style = MaterialTheme.typography.labelMedium,
                                        color = MaterialTheme.colorScheme.primary,
                                        modifier = Modifier.padding(end = 4.dp, bottom = 6.dp))
                                }
                                KitGroup {
                                    items.forEachIndexed { i, s ->
                                        if (i > 0) KitInsetDivider(start = 15.dp)
                                        val res = r?.results?.firstOrNull { it.id == s.id }
                                        Row(Modifier.fillMaxWidth().clickable {
                                            sel = if (s.id in sel) sel - s.id else sel + s.id
                                        }.padding(end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                                            Checkbox(checked = s.id in sel,
                                                onCheckedChange = { sel = if (it) sel + s.id else sel - s.id })
                                            Column(Modifier.weight(1f).padding(vertical = 10.dp)) {
                                                Text(s.name + if (s.slow) "（慢·管理员）" else "",
                                                    style = MaterialTheme.typography.bodyMedium,
                                                    color = MaterialTheme.colorScheme.onSurface)
                                                if (res != null && res.detail.isNotBlank()) {
                                                    // V272：显示完整多行日志（每步一行），不再截断 72 字——用户要详细日志
                                                    Text(res.detail.take(2000),
                                                        style = MaterialTheme.typography.bodySmall,
                                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                                }
                                            }
                                            if (res != null) {
                                                val (label, tone) = when {
                                                    res.skip -> "跳过" to MaterialTheme.colorScheme.onSurfaceVariant
                                                    res.ok -> "通过 ${res.ms}ms" to MaterialTheme.colorScheme.primary
                                                    else -> "失败" to MaterialTheme.colorScheme.error
                                                }
                                                Text(label, style = MaterialTheme.typography.labelMedium, color = tone)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        item { Spacer(Modifier.height(16.dp)) }
                    }
                }
            }
        }
    }
}
