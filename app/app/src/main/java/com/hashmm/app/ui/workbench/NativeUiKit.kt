package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** V221 原生模块 UI Kit（V244 分组化）—— 工作台子页共用的设计语言：
 *  ModuleHeader     返回 + 干净标题/副题 + 动作位
 *  StatTriple       顶部三格概览大数（页面第一眼有"仪表感"而非裸列表）
 *  StatusBadge      状态胶囊（成功/警示/错误/中性 四色调）
 *  KitGroup         分组白卡：一个分区的所有行装进同一张无边卡（Marvis「关于」页法）
 *  KitInsetDivider  行间发丝线（左缩过图标位）
 *  KitRow           分组卡内的行：裸图标 · 主副两级 · 右槽（V244 起不再各套一张卡）
 *  MetricRow        指标行：中文名 + 加粗值 + 说明副题（质量看板用，同为组内行） */

enum class KitTone { Primary, Success, Warn, Error, Neutral }

@Composable
private fun toneColor(t: KitTone): Color = when (t) {
    KitTone.Primary -> MaterialTheme.colorScheme.primary
    KitTone.Success -> Color(0xFF15803D)
    KitTone.Warn -> Color(0xFFB45309)
    KitTone.Error -> MaterialTheme.colorScheme.error
    KitTone.Neutral -> MaterialTheme.colorScheme.onSurfaceVariant
}

@Composable
fun ModuleHeader(icon: ImageVector, title: String, sub: String, onBack: () -> Unit, trailing: (@Composable () -> Unit)? = null) {
    Column(Modifier.statusBarsPadding()) {
        Row(
            Modifier.fillMaxWidth().padding(start = 4.dp, end = 14.dp, top = 4.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回") }
            Spacer(Modifier.width(1.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontSize = 20.sp, fontWeight = FontWeight.ExtraBold,
                    color = MaterialTheme.colorScheme.onSurface, letterSpacing = (-0.35).sp)
                if (sub.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 16.sp)
                }
            }
            trailing?.invoke()
        }
        Spacer(Modifier.height(6.dp))
    }
}

@Composable
fun StatTriple(a: Pair<String, String>, b: Pair<String, String>, c: Pair<String, String>, toneB: KitTone = KitTone.Primary) {
    Surface(
        color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(18.dp),
        // V243 Marvis 化：去描边，纯白平卡；圆角统一 16
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
    ) {
        Row(Modifier.fillMaxWidth().padding(vertical = 18.dp), verticalAlignment = Alignment.CenterVertically) {
            val cells = listOf(a to KitTone.Primary, b to toneB, c to KitTone.Neutral)
            cells.forEachIndexed { i, (p, t) ->
                Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(p.second, fontSize = 22.sp, fontWeight = FontWeight.ExtraBold,
                        color = toneColor(t), letterSpacing = (-0.5).sp)
                    Spacer(Modifier.height(3.dp))
                    Text(p.first, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (i < cells.size - 1) {
                    Box(Modifier.width(1.dp).height(30.dp)
                        .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)))
                }
            }
        }
    }
}

@Composable
fun StatusBadge(text: String, tone: KitTone) {
    if (text.isBlank()) return
    val c = toneColor(tone)
    // V243 Marvis 化：去 0.5dp 描边——参考稿的语气 chip（米色工具条/粉调标签）都是纯淡染无边
    Surface(color = c.copy(alpha = 0.12f), shape = RoundedCornerShape(50)) {
        Text(text, fontSize = 10.sp, fontWeight = FontWeight.Bold, color = c,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp))
    }
}

/** V244 分组白卡：一个分区的所有 KitRow/MetricRow 装进同一张无边卡。 */
@Composable
fun KitGroup(modifier: Modifier = Modifier, content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(18.dp),
        modifier = modifier.fillMaxWidth()) { Column(content = content) }
}

/** V244 行间发丝线：左缩过图标位。 */
@Composable
fun KitInsetDivider(start: androidx.compose.ui.unit.Dp = 63.dp) {
    Box(Modifier.fillMaxWidth().padding(start = start).height(0.5.dp)
        .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f)))
}

@Composable
fun ModuleSectionLabel(title: String, hint: String = "") {
    Row(
        Modifier.fillMaxWidth().padding(start = 2.dp, top = 4.dp, bottom = 7.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        Text(
            title,
            fontSize = 12.5.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.45.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (hint.isNotBlank()) {
            Spacer(Modifier.width(8.dp))
            Text(
                hint,
                fontSize = 10.5.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.78f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

/** 页面级判断卡：只概括已经拿到的真实数据，并提供一个明确的下一步。 */
@Composable
fun ModuleInsight(
    icon: ImageVector,
    title: String,
    body: String,
    tone: KitTone = KitTone.Primary,
    badge: String = "",
    actionLabel: String = "",
    onAction: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val color = toneColor(tone)
    Surface(
        color = color.copy(alpha = if (tone == KitTone.Primary) 0.08f else 0.10f),
        shape = RoundedCornerShape(18.dp),
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(horizontal = 15.dp, vertical = 14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(34.dp).background(color.copy(alpha = 0.13f), RoundedCornerShape(11.dp)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(18.dp))
                }
                Spacer(Modifier.width(10.dp))
                Text(
                    title,
                    fontSize = 14.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                if (badge.isNotBlank()) StatusBadge(badge, tone)
            }
            Spacer(Modifier.height(8.dp))
            Text(
                body,
                fontSize = 12.sp,
                lineHeight = 17.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (actionLabel.isNotBlank() && onAction != null) {
                TextButton(
                    onClick = onAction,
                    modifier = Modifier.align(Alignment.End).padding(top = 2.dp),
                ) {
                    Text(actionLabel, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}

@Composable
fun KitRow(
    icon: ImageVector, title: String, sub: String,
    tone: KitTone = KitTone.Primary,
    badge: String = "", badgeTone: KitTone = KitTone.Neutral,
    right: String = "",
    onClick: (() -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    val rowModifier = if (onClick == null) Modifier.fillMaxWidth()
        else Modifier.fillMaxWidth().clickable(onClick = onClick)
    Row(rowModifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            val iconColor = if (tone == KitTone.Primary) MaterialTheme.colorScheme.onSurface else toneColor(tone)
            Box(
                Modifier.size(36.dp).background(
                    if (tone == KitTone.Primary) MaterialTheme.colorScheme.surfaceVariant
                    else iconColor.copy(alpha = 0.10f),
                    RoundedCornerShape(11.dp),
                ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, null, tint = iconColor, modifier = Modifier.size(18.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(title, fontSize = 14.5.sp, fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.onSurface, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (badge.isNotBlank()) { Spacer(Modifier.width(7.dp)); StatusBadge(badge, badgeTone) }
                }
                if (sub.isNotBlank()) {
                    Spacer(Modifier.height(3.dp))
                    Text(sub, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 15.sp)
                }
            }
            if (right.isNotBlank()) {
                Spacer(Modifier.width(10.dp))
                Text(right, fontSize = 11.sp, fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            trailing?.invoke()
    }
}

@Composable
fun MetricRow(label: String, value: String, hint: String = "", tone: KitTone = KitTone.Primary) {
    // V244：分组卡内的指标行；数值直接加粗字（Primary＝墨黑，警示/错误保留语义色）
    Row(Modifier.fillMaxWidth().padding(start = 15.dp, end = 15.dp, top = 13.dp, bottom = 13.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(label, fontSize = 13.5.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface)
                if (hint.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(hint, fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, lineHeight = 14.sp)
                }
            }
            Spacer(Modifier.width(10.dp))
            Text(value, fontSize = 14.sp, fontWeight = FontWeight.Bold,
                color = if (tone == KitTone.Primary) MaterialTheme.colorScheme.onSurface else toneColor(tone),
                letterSpacing = (-0.2).sp, lineHeight = 18.sp, maxLines = 3,
                overflow = TextOverflow.Ellipsis, textAlign = TextAlign.End,
                modifier = Modifier.widthIn(max = 190.dp))
    }
}

/** 秒级时间 → 相对时间（Kit 内自带，页间共用）。 */
fun kitAgo(sec: Long): String {
    if (sec <= 0) return ""
    val d = (System.currentTimeMillis() / 1000 - sec).coerceAtLeast(0)
    return when {
        d < 60 -> "刚刚"; d < 3600 -> "${d / 60}分钟前"; d < 86400 -> "${d / 3600}小时前"; else -> "${d / 86400}天前"
    }
}
