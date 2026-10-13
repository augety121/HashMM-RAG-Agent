package com.hashmm.app.ui.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** V221 原生模块 UI Kit —— 六个工作台页共用的设计语言（对齐主流工具类 App）：
 *  ModuleHeader   图标徽 + 大标题/副题 + 返回/动作位
 *  StatTriple     顶部三格概览大数（页面第一眼有"仪表感"而非裸列表）
 *  StatusBadge    状态胶囊（成功/警示/错误/中性 四色调）
 *  KitRow         统一行卡：36dp 淡染图标容器 · 主副两级 · 右槽（时间/徽章）
 *  MetricRow      指标行：中文名 + 格式化值 + 说明副题（质量看板用） */

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
    Row(
        Modifier.fillMaxWidth().padding(start = 8.dp, end = 16.dp, top = 10.dp, bottom = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回") }
        Box(
            Modifier.size(40.dp).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f), RoundedCornerShape(12.dp)),
            contentAlignment = Alignment.Center,
        ) { Icon(icon, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(21.dp)) }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
            Text(sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        trailing?.invoke()
    }
}

@Composable
fun StatTriple(a: Pair<String, String>, b: Pair<String, String>, c: Pair<String, String>, toneB: KitTone = KitTone.Primary) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
    ) {
        Row(Modifier.fillMaxWidth().padding(vertical = 14.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
            listOf(a to KitTone.Primary, b to toneB, c to KitTone.Neutral).forEach { (p, t) ->
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(p.second, fontSize = 19.sp, fontWeight = FontWeight.ExtraBold, color = toneColor(t))
                    Spacer(Modifier.height(2.dp))
                    Text(p.first, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
fun StatusBadge(text: String, tone: KitTone) {
    if (text.isBlank()) return
    val c = toneColor(tone)
    Surface(color = c.copy(alpha = 0.13f), shape = RoundedCornerShape(50)) {
        Text(text, fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold, color = c,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp))
    }
}

@Composable
fun KitRow(
    icon: ImageVector, title: String, sub: String,
    tone: KitTone = KitTone.Primary,
    badge: String = "", badgeTone: KitTone = KitTone.Neutral,
    right: String = "",
) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 13.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(36.dp).background(toneColor(tone).copy(alpha = 0.12f), RoundedCornerShape(10.dp)),
                contentAlignment = Alignment.Center,
            ) { Icon(icon, null, tint = toneColor(tone), modifier = Modifier.size(18.dp)) }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.onSurface, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (badge.isNotBlank()) { Spacer(Modifier.width(7.dp)); StatusBadge(badge, badgeTone) }
                }
                if (sub.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(sub, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
            if (right.isNotBlank()) {
                Spacer(Modifier.width(8.dp))
                Text(right, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
fun MetricRow(label: String, value: String, hint: String = "", tone: KitTone = KitTone.Primary) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(13.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(label, fontSize = 13.5.sp, color = MaterialTheme.colorScheme.onSurface)
                if (hint.isNotBlank()) {
                    Spacer(Modifier.height(1.dp))
                    Text(hint, fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Text(value, fontSize = 16.sp, fontWeight = FontWeight.ExtraBold, color = toneColor(tone))
        }
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
