package com.hashmm.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * HashMM 字体尺度（V206）—— 对标大厂移动端排版（iOS HIG / Material 3 的实际落地值），
 * 收敛原来散落在各页的 19 种魔法字号（10sp~36sp）到一套语义化梯度。
 *
 * 设计原则（大厂移动端通行）：
 *   - 正文基准 15sp（中文更耐读的中庸取值）；相邻级差 1~2sp，不跳档；
 *   - 行高 = 字号 × ~1.4（中文舒适行距）；
 *   - 页面大标题 22sp 封顶——移动端不需要 28/34/36 这种桌面级巨字；
 *   - 字重只用 Regular/Medium/SemiBold/Bold 四档，不用 ExtraBold（移动端偏重会糊）。
 *
 * 用法：Text(..., style = AppType.body) 或 Text(..., fontSize = AppFont.body)。
 */

/** 纯字号常量（迁移期用；等价于 AppType 各级的 fontSize）。 */
object AppFont {
    val screenTitle = 22.sp   // 页面主标题（每屏唯一）
    val sectionTitle = 17.sp  // 卡片区/大分区标题
    val cardTitle = 15.sp     // 列表项/卡片标题（= 正文基准）
    val body = 15.sp          // 正文
    val subtitle = 13.sp      // 副标题/次要说明
    val caption = 12.sp       // 辅助信息/时间戳
    val label = 11.sp         // 标签/角标（最小可读下限）
}

/** 语义化文本样式（推荐新代码使用）。 */
object AppType {
    val screenTitle = TextStyle(fontSize = 22.sp, lineHeight = 29.sp, fontWeight = FontWeight.Bold)
    val sectionTitle = TextStyle(fontSize = 17.sp, lineHeight = 23.sp, fontWeight = FontWeight.SemiBold)
    val cardTitle = TextStyle(fontSize = 15.sp, lineHeight = 21.sp, fontWeight = FontWeight.SemiBold)
    val body = TextStyle(fontSize = 15.sp, lineHeight = 22.sp, fontWeight = FontWeight.Normal)
    val bodyMedium = TextStyle(fontSize = 15.sp, lineHeight = 22.sp, fontWeight = FontWeight.Medium)
    val subtitle = TextStyle(fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.Normal)
    val caption = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Normal)
    val label = TextStyle(fontSize = 11.sp, lineHeight = 15.sp, fontWeight = FontWeight.Medium)
}

/**
 * Material3 Typography：把上面的尺度接进 MaterialTheme.typography，
 * 让默认用 typography 的 M3 组件（Button/TopAppBar 等）也自动对齐。
 */
val Typography = Typography(
    headlineSmall = AppType.screenTitle,
    titleLarge = AppType.sectionTitle,
    titleMedium = AppType.cardTitle,
    bodyLarge = AppType.body,
    bodyMedium = AppType.subtitle,
    bodySmall = AppType.caption,
    labelSmall = AppType.label,
)
