package com.hashmm.app.ui.tasks

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccountTree
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Computer
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.hashmm.app.ui.components.ScreenHeader
import com.hashmm.app.ui.workbench.WorkbenchHubViewModel

private data class TaskDefinition(
    val title: String,
    val subtitle: String,
    val icon: ImageVector,
    val placeholder: String,
    val dispatchKind: String,
)

/**
 * 电脑任务的统一创建页。这里的选项会被写入真实任务文本，并经由
 * Chat -> 后端持久队列 -> 桌面执行器 -> 同一 Chat 回传结果，不是本地演示状态。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ComputerTaskLaunchScreen(
    kind: String,
    preset: String,
    onBack: () -> Unit,
    onLaunch: (String, String) -> Unit,
    viewModel: WorkbenchHubViewModel = hiltViewModel(),
) {
    val definition = taskDefinition(kind)
    val status by viewModel.ui.collectAsStateWithLifecycle()
    var task by rememberSaveable(kind, preset) { mutableStateOf(preset) }
    var primaryOption by rememberSaveable(kind) { mutableStateOf(defaultPrimaryOption(kind)) }
    var secondaryOption by rememberSaveable(kind) { mutableStateOf(defaultSecondaryOption(kind)) }
    var includeEvidence by rememberSaveable(kind) { mutableStateOf(kind == "browser") }
    val canSubmit = status.backendOnline && task.trim().length >= 4

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            ScreenHeader(
                title = definition.title,
                subtitle = "创建后进入对话，可离开页面继续执行",
                onBack = onBack,
            )
        },
    ) { inner ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(inner)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            TaskHero(definition = definition, backendOnline = status.backendOnline,
                desktopOnline = status.desktopOnline, desktopCount = status.desktopCount)

            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(20.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    Column {
                        Text("任务要求", fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(3.dp))
                        Text(definition.subtitle, fontSize = 12.sp, lineHeight = 17.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    OutlinedTextField(
                        value = task,
                        onValueChange = { task = it },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 4,
                        maxLines = 8,
                        placeholder = { Text(definition.placeholder, fontSize = 13.sp) },
                        shape = RoundedCornerShape(16.dp),
                    )

                    when (kind) {
                        "browser" -> {
                            OptionBlock("调研深度", listOf("快速", "标准", "深入"), primaryOption) { primaryOption = it }
                            OptionBlock("交付格式", listOf("要点清单", "对比表", "结构化报告"), secondaryOption) { secondaryOption = it }
                            SwitchRow("保留来源证据", "要求输出来源页面标题和链接，无法验证的内容明确标注", includeEvidence) {
                                includeEvidence = it
                            }
                        }
                        "file" -> {
                            OptionBlock("查找范围", listOf("桌面与下载", "文档目录", "全部常用目录"), primaryOption) { primaryOption = it }
                            OptionBlock("筛选条件", listOf("最近 7 天", "PDF 与 Word", "图片", "压缩包"), secondaryOption) { secondaryOption = it }
                        }
                        "seq" -> {
                            OptionBlock("执行方式", listOf("先规划再执行", "逐步确认", "仅生成方案"), primaryOption) { primaryOption = it }
                            SwitchRow("保留变更清单", "执行前后记录文件和系统变更，失败步骤可定位", includeEvidence) {
                                includeEvidence = it
                            }
                        }
                        else -> {
                            OptionBlock("检查范围", listOf("常用目录", "桌面状态", "文件与环境"), primaryOption) { primaryOption = it }
                            SwitchRow("只读检查", "不修改文件、不运行高风险命令，只返回清单", true, enabled = false) { }
                        }
                    }
                }
            }

            ExecutionPathCard(desktopOnline = status.desktopOnline)

            Button(
                onClick = {
                    val prompt = buildComputerTaskPrompt(
                        kind = kind,
                        task = task,
                        primaryOption = primaryOption,
                        secondaryOption = secondaryOption,
                        includeEvidence = includeEvidence,
                    )
                    onLaunch(prompt, definition.dispatchKind)
                },
                enabled = canSubmit,
                modifier = Modifier.fillMaxWidth().height(50.dp),
                shape = RoundedCornerShape(15.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = Color.White,
                ),
            ) {
                Text(
                    when {
                        !status.backendOnline -> "后端不可达，暂不能创建"
                        status.desktopOnline -> "创建任务并开始执行"
                        else -> "创建任务，等待电脑上线"
                    },
                    fontWeight = FontWeight.SemiBold,
                )
            }
            if (task.isNotBlank() && task.trim().length < 4) {
                Text("请至少写清楚一个可执行目标。", fontSize = 11.5.sp,
                    color = MaterialTheme.colorScheme.error)
            }
            Spacer(Modifier.height(18.dp))
        }
    }
}

@Composable
private fun TaskHero(
    definition: TaskDefinition,
    backendOnline: Boolean,
    desktopOnline: Boolean,
    desktopCount: Int,
) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surface, shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(46.dp).background(cs.surfaceVariant, RoundedCornerShape(14.dp)),
                contentAlignment = Alignment.Center) {
                Icon(definition.icon, null, tint = cs.onSurface, modifier = Modifier.size(23.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(definition.title, fontSize = 17.sp, fontWeight = FontWeight.ExtraBold)
                Spacer(Modifier.height(3.dp))
                val (label, color) = when {
                    !backendOnline -> "后端不可达" to cs.error
                    desktopOnline -> "${desktopCount.coerceAtLeast(1)} 台电脑在线" to Color(0xFF15803D)
                    else -> "电脑离线，任务将持久排队" to Color(0xFFB45309)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(7.dp).background(color, CircleShape))
                    Spacer(Modifier.width(6.dp))
                    Text(label, fontSize = 12.sp, color = color, fontWeight = FontWeight.Medium)
                }
            }
        }
    }
}

@Composable
private fun OptionBlock(label: String, options: List<String>, selected: String, onSelect: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
        Text(label, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            options.forEach { option ->
                FilterChip(selected = option == selected, onClick = { onSelect(option) }, label = { Text(option) })
            }
        }
    }
}

@Composable
private fun SwitchRow(label: String, description: String, checked: Boolean, enabled: Boolean = true, onChecked: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Text(description, fontSize = 11.5.sp, lineHeight = 16.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = checked, onCheckedChange = if (enabled) onChecked else null, enabled = enabled)
    }
}

@Composable
private fun ExecutionPathCard(desktopOnline: Boolean) {
    val cs = MaterialTheme.colorScheme
    Surface(color = cs.surface, shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("执行闭环", fontSize = 14.sp, fontWeight = FontWeight.Bold)
            PathRow(Icons.Outlined.Schedule, "持久排队", if (desktopOnline) "创建后由在线电脑立即认领" else "电脑离线也不会丢失，上线后继续")
            PathRow(Icons.Outlined.Security, "受控执行", "敏感操作要求确认，过程状态可追踪")
            PathRow(Icons.Outlined.CheckCircle, "回到同一对话", "进度、失败原因、文件和最终结果统一回传")
        }
    }
}

@Composable
private fun PathRow(icon: ImageVector, title: String, subtitle: String) {
    Row(verticalAlignment = Alignment.Top) {
        Icon(icon, null, modifier = Modifier.size(19.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(10.dp))
        Column {
            Text(title, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Text(subtitle, fontSize = 11.5.sp, lineHeight = 16.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun taskDefinition(kind: String): TaskDefinition = when (kind) {
    "browser" -> TaskDefinition(
        "浏览器调研", "桌面端搜索并阅读多个页面，结论与来源回到新对话。",
        Icons.Outlined.Language, "例：调研三款 RAG Agent 产品，比较检索、工具调用与长任务能力。", "browser_use",
    )
    "file" -> TaskDefinition(
        "从电脑取文件", "按目录、名称、类型和时间查找，找到后发送到新对话。",
        Icons.Outlined.FolderOpen, "例：查找下载目录最近 7 天修改的项目方案 PDF 和 Word 文件。", "file",
    )
    "seq" -> TaskDefinition(
        "多步电脑任务", "先形成可审查计划，再由桌面端逐步执行并回传。",
        Icons.Outlined.AccountTree, "例：整理下载目录，按类型分类；重复文件只列清单，不直接删除。", "seq",
    )
    else -> TaskDefinition(
        "电脑只读检查", "读取电脑状态和文件清单，不执行破坏性操作。",
        Icons.Outlined.Computer, "例：列出桌面、下载和文档目录中的文件，并按修改时间排序。", "computer_use",
    )
}

private fun defaultPrimaryOption(kind: String): String = when (kind) {
    "browser" -> "标准"
    "file" -> "桌面与下载"
    "seq" -> "先规划再执行"
    else -> "常用目录"
}

private fun defaultSecondaryOption(kind: String): String = when (kind) {
    "browser" -> "结构化报告"
    "file" -> "最近 7 天"
    else -> ""
}

internal fun buildComputerTaskPrompt(
    kind: String,
    task: String,
    primaryOption: String,
    secondaryOption: String,
    includeEvidence: Boolean,
): String {
    val cleanTask = task.trim()
    return when (kind) {
        "browser" -> buildString {
            append(cleanTask)
            append("\n\n执行要求：调研深度=").append(primaryOption)
            append("；交付格式=").append(secondaryOption)
            if (includeEvidence) append("；保留来源页面标题、链接和关键证据，无法验证的内容明确标注。")
            else append("；区分事实、推断和未知项。")
        }
        "file" -> "$cleanTask\n\n查找范围=$primaryOption；筛选条件=$secondaryOption；只发送匹配文件，找不到时返回已检查目录和未找到原因。"
        "seq" -> "$cleanTask\n\n执行方式=$primaryOption；${if (includeEvidence) "记录执行计划、每步状态和变更清单；" else "记录每步状态；"}高风险或不可逆操作必须先请求确认，失败时停止并回报原因。"
        else -> "$cleanTask\n\n检查范围=$primaryOption；仅执行只读检查，不修改、移动或删除文件，不运行高风险命令；结果按路径和修改时间整理。"
    }
}
