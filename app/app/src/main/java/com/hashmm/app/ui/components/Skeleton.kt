package com.hashmm.app.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.hashmm.app.ui.theme.AppShape
import com.hashmm.app.ui.theme.AppSize
import com.hashmm.app.ui.theme.AppSpacing

/**
 * 骨架屏 / Shimmer 占位（V300 第一期）—— 加载时的大厂标配。
 *
 * 此前 App 加载多是转圈（CircularProgressIndicator）或空白；大厂 App 用"内容形状的骨架 + 微光扫过"，
 * 让用户预知即将出现的内容结构、感知更快。列表/卡片加载都用这里的骨架，而非转圈。
 */

/** 单个骨架块（微光扫过的圆角矩形）。 */
@Composable
fun HmmSkeletonBox(modifier: Modifier = Modifier, height: Dp = 16.dp, widthFraction: Float = 1f) {
    val cs = MaterialTheme.colorScheme
    val transition = rememberInfiniteTransition(label = "shimmer")
    val x by transition.animateFloat(
        initialValue = -300f, targetValue = 900f,
        animationSpec = infiniteRepeatable(tween(1200), RepeatMode.Restart), label = "shimmerX",
    )
    val base = cs.surfaceVariant
    val highlight = cs.surfaceVariant.copy(alpha = 0.4f)
    val brush = Brush.linearGradient(
        colors = listOf(base, highlight, base),
        start = Offset(x, 0f), end = Offset(x + 300f, 0f),
    )
    Box(
        modifier
            .then(if (widthFraction >= 1f) Modifier.fillMaxWidth() else Modifier.fillMaxWidth(widthFraction))
            .height(height).clip(AppShape.small).background(brush),
    )
}

/** 列表项骨架（图标容器 + 两行文字），匹配 HmmListItem 的形状。 */
@Composable
fun HmmSkeletonListItem(modifier: Modifier = Modifier, showIcon: Boolean = true) {
    Row(
        modifier.fillMaxWidth().padding(horizontal = AppSpacing.md, vertical = AppSpacing.md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (showIcon) {
            Box(Modifier.size(AppSize.iconBox).clip(AppShape.iconBox).background(MaterialTheme.colorScheme.surfaceVariant))
            Spacer(Modifier.width(AppSpacing.md))
        }
        Column(Modifier.weight(1f)) {
            HmmSkeletonBox(height = 15.dp, widthFraction = 0.55f)
            Spacer(Modifier.height(AppSpacing.sm))
            HmmSkeletonBox(height = 12.dp, widthFraction = 0.8f)
        }
    }
}

/** 卡片骨架（标题行 + 若干正文行）。 */
@Composable
fun HmmSkeletonCard(modifier: Modifier = Modifier, lines: Int = 3) {
    HmmCard(modifier = modifier) {
        HmmSkeletonBox(height = 17.dp, widthFraction = 0.4f)
        Spacer(Modifier.height(AppSpacing.md))
        repeat(lines) { i ->
            HmmSkeletonBox(height = 13.dp, widthFraction = if (i == lines - 1) 0.6f else 1f)
            if (i < lines - 1) Spacer(Modifier.height(AppSpacing.sm))
        }
    }
}

/** 列表加载骨架（n 个列表项），直接替换"转圈"。 */
@Composable
fun HmmSkeletonList(count: Int = 6, showIcon: Boolean = true) {
    Column(Modifier.fillMaxWidth()) {
        repeat(count) { HmmSkeletonListItem(showIcon = showIcon) }
    }
}
