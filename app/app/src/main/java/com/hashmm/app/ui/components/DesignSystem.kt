package com.hashmm.app.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.hashmm.app.ui.theme.AppMotion
import com.hashmm.app.ui.theme.AppRadius
import com.hashmm.app.ui.theme.AppShape
import com.hashmm.app.ui.theme.AppSize
import com.hashmm.app.ui.theme.AppSpacing
import com.hashmm.app.ui.theme.AppType

/**
 * HashMM App 设计系统（V300 第一期）—— Compose 版组件库，对齐桌面端 PanelKit。
 *
 * 目标：把 App 从"每页各写原始布局"（此前 47 个 UI 文件仅 2 个共享组件）改成
 * "页面只组合组件"。所有卡片/按钮/列表项/空态/徽章走这里，视觉一次统一、改动一处生效。
 *
 * 组件清单（与桌面 PanelKit 一一对应 + 移动端补充）：
 *   HmmCard / HmmCardHeader   卡片 + 卡片头
 *   HmmButton                 按钮（primary/secondary/ghost/danger 四态，含 busy）
 *   HmmStatCard               指标卡（label + 大数值 + 图标）
 *   HmmSectionTitle           区块标题（左标题 + 右操作）
 *   HmmBadge                  徽章（neutral/accent/success/warning/error）
 *   HmmIconBadge              淡染图标方块容器（列表项左侧统一视觉）
 *   HmmListItem               列表项（图标容器 + 标题 + 副标题 + 右侧内容/箭头）
 *   HmmStateView              三态占位（loading/empty/error，error 带重试）
 *   HmmChip                   胶囊标签（可点）
 *   HmmDivider                分隔线
 */

// ─────────────────────────── 卡片 ───────────────────────────
@Composable
fun HmmCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    padding: Boolean = true,
    content: @Composable ColumnScope.() -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    Surface(
        color = cs.surface,
        shape = AppShape.card,
        // V243 Marvis 化：去 1dp 描边——参考稿的卡片是纯白无边浮在 #F4F4F5 上；
        // 描边是"线框稿感"的最大来源，去掉后全 App（我的/动态/工作台/…）一次生效。
        modifier = modifier.fillMaxWidth().clip(AppShape.card)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
    ) {
        Column(Modifier.padding(if (padding) AppSpacing.lg else 0.dp), content = content)
    }
}

// ─────────────────────── 回弹按压（Marvis 活泼元气） ───────────────────────
/**
 * V243 统一微交互：按下缩到 0.965，松手以带过冲的 spring 弹回——
 * 呼应品牌"活泼元气"（对话页黑胶囊按钮同款手感）。只给"独立可点卡片"用；
 * 分组卡内部的行仍走标准 bounded 涟漪（大厂列表行的正确反馈）。
 */
@Composable
fun Modifier.pressBounce(interaction: MutableInteractionSource): Modifier {
    val pressed by interaction.collectIsPressedAsState()
    val scale by androidx.compose.animation.core.animateFloatAsState(
        targetValue = if (pressed) 0.965f else 1f,
        animationSpec = if (pressed) androidx.compose.animation.core.tween(90)
        else androidx.compose.animation.core.spring(dampingRatio = 0.55f, stiffness = 380f),
        label = "pressBounce",
    )
    return this.scale(scale)
}

@Composable
fun HmmCardHeader(title: String, sub: String? = null, icon: ImageVector? = null, right: @Composable (() -> Unit)? = null) {
    val cs = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        if (icon != null) {
            HmmIconBadge(icon)
            Spacer(Modifier.width(AppSpacing.md))
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = AppType.cardTitle, color = cs.onSurface)
            if (sub != null) {
                Spacer(Modifier.height(2.dp))
                Text(sub, style = AppType.subtitle, color = cs.onSurfaceVariant)
            }
        }
        if (right != null) right()
    }
}

// ─────────────────────────── 按钮 ───────────────────────────
enum class HmmButtonVariant { Primary, Secondary, Ghost, Danger }

@Composable
fun HmmButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    variant: HmmButtonVariant = HmmButtonVariant.Secondary,
    icon: ImageVector? = null,
    enabled: Boolean = true,
    busy: Boolean = false,
) {
    val cs = MaterialTheme.colorScheme
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val scale by animateFloatAsState(if (pressed) 0.97f else 1f, AppMotion.tweenFast(), label = "btnScale")

    val (bg, fg, border) = when (variant) {
        HmmButtonVariant.Primary -> Triple(cs.primary, cs.onPrimary, null)
        HmmButtonVariant.Secondary -> Triple(cs.surfaceVariant, cs.onSurface, null)
        HmmButtonVariant.Ghost -> Triple(Color.Transparent, cs.onSurfaceVariant, BorderStroke(1.dp, cs.outline))
        HmmButtonVariant.Danger -> Triple(cs.errorContainer, cs.error, null)
    }
    val active = enabled && !busy
    Surface(
        color = if (active) bg else bg.copy(alpha = 0.5f),
        shape = AppShape.small,
        border = border,
        modifier = modifier.scale(scale).clip(AppShape.small)
            .then(if (active) Modifier.clickable(interactionSource = interaction, indication = null, onClick = onClick) else Modifier),
    ) {
        Row(
            Modifier.padding(horizontal = AppSpacing.lg, vertical = AppSpacing.md),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (busy) {
                CircularProgressIndicator(Modifier.size(16.dp), color = fg, strokeWidth = 2.dp)
                Spacer(Modifier.width(AppSpacing.sm))
            } else if (icon != null) {
                Icon(icon, contentDescription = null, tint = fg, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(AppSpacing.sm))
            }
            Text(text, style = AppType.bodyMedium, color = if (active) fg else fg.copy(alpha = 0.6f))
        }
    }
}

// ─────────────────────────── 指标卡 ───────────────────────────
enum class HmmTone { Default, Accent, Success, Warning, Error }

@Composable
private fun toneColor(tone: HmmTone): Color {
    val cs = MaterialTheme.colorScheme
    return when (tone) {
        HmmTone.Default -> cs.onSurface
        HmmTone.Accent -> cs.primary
        HmmTone.Success -> Color(0xFF22A06B)
        HmmTone.Warning -> Color(0xFFE5A50A)
        HmmTone.Error -> cs.error
    }
}

@Composable
fun HmmStatCard(label: String, value: String, modifier: Modifier = Modifier, unit: String? = null, icon: ImageVector? = null, tone: HmmTone = HmmTone.Default) {
    val cs = MaterialTheme.colorScheme
    HmmCard(modifier = modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = AppType.caption, color = cs.onSurfaceVariant, modifier = Modifier.weight(1f))
            if (icon != null) Icon(icon, contentDescription = null, tint = cs.onSurfaceVariant, modifier = Modifier.size(16.dp))
        }
        Spacer(Modifier.height(AppSpacing.sm))
        Row(verticalAlignment = Alignment.Bottom) {
            Text(value, fontSize = 24.sp, fontWeight = FontWeight.Bold, color = toneColor(tone))
            if (unit != null) {
                Spacer(Modifier.width(3.dp))
                Text(unit, style = AppType.caption, color = cs.onSurfaceVariant, modifier = Modifier.padding(bottom = 3.dp))
            }
        }
    }
}

// ─────────────────────────── 区块标题 ───────────────────────────
@Composable
fun HmmSectionTitle(title: String, modifier: Modifier = Modifier, right: @Composable (() -> Unit)? = null) {
    val cs = MaterialTheme.colorScheme
    Row(
        modifier.fillMaxWidth().padding(top = AppSpacing.lg, bottom = AppSpacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, style = AppType.sectionTitle, color = cs.onSurface, modifier = Modifier.weight(1f))
        if (right != null) right()
    }
}

// ─────────────────────────── 徽章 ───────────────────────────
@Composable
fun HmmBadge(text: String, tone: HmmTone = HmmTone.Default) {
    val base = toneColor(if (tone == HmmTone.Default) HmmTone.Accent else tone)
    Surface(color = base.copy(alpha = 0.12f), shape = AppShape.chip) {
        Text(text, style = AppType.label, color = base, modifier = Modifier.padding(horizontal = AppSpacing.sm, vertical = 3.dp))
    }
}

// ─────────────────────────── 淡染图标容器 ───────────────────────────
@Composable
fun HmmIconBadge(icon: ImageVector, tint: Color? = null, containerColor: Color? = null) {
    val cs = MaterialTheme.colorScheme
    Box(
        Modifier.size(AppSize.iconBox).clip(AppShape.iconBox)
            .background(containerColor ?: cs.surfaceVariant),
        contentAlignment = Alignment.Center,
    ) { Icon(icon, contentDescription = null, tint = tint ?: cs.primary, modifier = Modifier.size(AppSize.iconInBox)) }
}

// ─────────────────────────── 列表项 ───────────────────────────
@Composable
fun HmmListItem(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    icon: ImageVector? = null,
    iconTint: Color? = null,
    onClick: (() -> Unit)? = null,
    trailing: @Composable (() -> Unit)? = null,
    enabled: Boolean = true,
) {
    val cs = MaterialTheme.colorScheme
    Row(
        modifier.fillMaxWidth().clip(AppShape.small)
            .then(if (onClick != null && enabled) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = AppSpacing.md, vertical = AppSpacing.md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) {
            HmmIconBadge(icon, tint = iconTint)
            Spacer(Modifier.width(AppSpacing.md))
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = AppType.cardTitle, color = if (enabled) cs.onSurface else cs.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) {
                Spacer(Modifier.height(2.dp))
                Text(subtitle, style = AppType.subtitle, color = cs.onSurfaceVariant,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        if (trailing != null) {
            Spacer(Modifier.width(AppSpacing.sm))
            trailing()
        }
    }
}

// ─────────────────────────── 胶囊标签 ───────────────────────────
@Composable
fun HmmChip(text: String, onClick: (() -> Unit)? = null, selected: Boolean = false) {
    val cs = MaterialTheme.colorScheme
    // V244 Marvis 化：选中态＝墨黑实心 + 白字（参考稿分类 chip 的选中语言），未选中＝白底灰字
    val bg = if (selected) cs.primary else cs.surface
    val fg = if (selected) cs.onPrimary else cs.onSurfaceVariant
    Surface(
        color = bg, shape = AppShape.chip,
        modifier = Modifier.clip(AppShape.chip).then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
    ) {
        Text(text, style = AppType.subtitle, color = fg,
            fontWeight = if (selected) androidx.compose.ui.text.font.FontWeight.SemiBold else androidx.compose.ui.text.font.FontWeight.Normal,
            modifier = Modifier.padding(horizontal = AppSpacing.md, vertical = AppSpacing.sm))
    }
}

// ─────────────────────────── 分隔线 ───────────────────────────
@Composable
fun HmmDivider(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(1.dp).background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)))
}

// ─────────────────────────── 三态占位 ───────────────────────────
enum class HmmStateKind { Loading, Empty, Error }

@Composable
fun HmmStateView(
    kind: HmmStateKind,
    modifier: Modifier = Modifier,
    title: String? = null,
    message: String? = null,
    icon: ImageVector? = null,
    onRetry: (() -> Unit)? = null,
    action: @Composable (() -> Unit)? = null,
) {
    val cs = MaterialTheme.colorScheme
    Column(
        modifier.fillMaxWidth().padding(vertical = AppSpacing.xxl, horizontal = AppSpacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        when (kind) {
            HmmStateKind.Loading -> {
                CircularProgressIndicator(Modifier.size(28.dp), color = cs.primary, strokeWidth = 2.5.dp)
                if (message != null) {
                    Spacer(Modifier.height(AppSpacing.md))
                    Text(message, style = AppType.subtitle, color = cs.onSurfaceVariant)
                }
            }
            else -> {
                if (icon != null) {
                    Box(
                        Modifier.size(52.dp).clip(CircleShape).background(cs.surfaceVariant),
                        contentAlignment = Alignment.Center,
                    ) { Icon(icon, contentDescription = null, tint = cs.onSurfaceVariant, modifier = Modifier.size(26.dp)) }
                    Spacer(Modifier.height(AppSpacing.md))
                }
                if (title != null) {
                    Text(title, style = AppType.cardTitle, color = cs.onSurface)
                    Spacer(Modifier.height(AppSpacing.xs))
                }
                if (message != null) {
                    Text(message, style = AppType.subtitle, color = cs.onSurfaceVariant)
                }
                if (kind == HmmStateKind.Error && onRetry != null) {
                    Spacer(Modifier.height(AppSpacing.lg))
                    HmmButton("重试", onRetry, variant = HmmButtonVariant.Secondary)
                }
                if (action != null) {
                    Spacer(Modifier.height(AppSpacing.lg))
                    action()
                }
            }
        }
    }
}

// ─────────────────────────── 输入弹窗（V245） ───────────────────────────
/**
 * HmmPromptDialog —— 品牌化单输入弹窗，替代系统默认 AlertDialog（灰底描边输入框的"素"样式）。
 * 白色 22 圆角卡 + 墨黑标题 + 灰副题 + 浅灰容器输入 + 「取消」文字键 / 墨黑实心确认键。
 * 动态页快捷指挥、高级能力派活等所有"输一句话就派活"的场景共用。
 */
@Composable
fun HmmPromptDialog(
    title: String,
    subtitle: String = "",
    placeholder: String = "",
    confirmLabel: String = "派活",
    initialText: String = "",
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    var text by remember { androidx.compose.runtime.mutableStateOf(initialText) }
    androidx.compose.ui.window.Dialog(onDismissRequest = onDismiss) {
        Surface(color = cs.surface, shape = androidx.compose.foundation.shape.RoundedCornerShape(22.dp)) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 20.dp)) {
                Text(title, fontSize = 17.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.Bold,
                    color = cs.onSurface, letterSpacing = (-0.3).sp)
                if (subtitle.isNotBlank()) {
                    Spacer(Modifier.height(4.dp))
                    Text(subtitle, fontSize = 12.5.sp, color = cs.onSurfaceVariant, lineHeight = 17.sp)
                }
                Spacer(Modifier.height(14.dp))
                Surface(color = cs.surfaceVariant.copy(alpha = 0.6f),
                    shape = androidx.compose.foundation.shape.RoundedCornerShape(14.dp)) {
                    Box(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 13.dp)) {
                        if (text.isEmpty()) Text(placeholder, fontSize = 14.sp,
                            color = cs.onSurfaceVariant.copy(alpha = 0.8f))
                        androidx.compose.foundation.text.BasicTextField(
                            value = text, onValueChange = { text = it },
                            textStyle = androidx.compose.ui.text.TextStyle(
                                fontSize = 14.sp, color = cs.onSurface, lineHeight = 20.sp),
                            cursorBrush = androidx.compose.ui.graphics.SolidColor(cs.onSurface),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
                Spacer(Modifier.height(18.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("取消", fontSize = 14.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.Medium,
                        color = cs.onSurfaceVariant,
                        modifier = Modifier.clip(androidx.compose.foundation.shape.RoundedCornerShape(50))
                            .clickable(onClick = onDismiss).padding(horizontal = 14.dp, vertical = 9.dp))
                    Spacer(Modifier.width(6.dp))
                    val enabled = text.isNotBlank()
                    Surface(
                        color = if (enabled) cs.primary else cs.surfaceVariant,
                        shape = androidx.compose.foundation.shape.RoundedCornerShape(50),
                        modifier = Modifier.clip(androidx.compose.foundation.shape.RoundedCornerShape(50))
                            .clickable(enabled = enabled) { onConfirm(text.trim()) },
                    ) {
                        Text(confirmLabel, fontSize = 14.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold,
                            color = if (enabled) cs.onPrimary else cs.onSurfaceVariant,
                            modifier = Modifier.padding(horizontal = 20.dp, vertical = 9.dp))
                    }
                }
            }
        }
    }
}
