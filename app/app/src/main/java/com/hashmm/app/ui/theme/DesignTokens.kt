package com.hashmm.app.ui.theme

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.dp

/**
 * HashMM App 设计令牌（V300 第一期）—— 把散落在各页的魔法数字（间距/圆角/时长）收敛成一套语义化令牌。
 *
 * 为什么要它：此前 47 个 UI 文件各写各的 padding/圆角/动画时长，视觉不统一、改动要逐页改。
 * 一套令牌 + 一套组件（见 components/DesignSystem.kt）后，页面只组合组件、引用令牌，不再写原始数值。
 *
 * 设计原则（对标 iOS HIG / Material 3 落地值）：
 *   - 间距走 4 的倍数梯度（4/8/12/16/20/24），克制不跳档；
 *   - 圆角分级：小元件 10、卡片 16、大容器 20、胶囊 50；
 *   - 动效统一时长 + 缓动曲线，页面/列表/反馈用同一套（避免"每处动画都不一样"的廉价感）。
 */

/** 间距梯度（dp）。语义化：xs=紧凑内间距，md=卡片内边距，lg=区块间距。 */
object AppSpacing {
    val xs = 4.dp      // 图标与文字、徽章内边距
    val sm = 8.dp      // 元件间小间隙
    val md = 12.dp     // 列表项内边距、元件间常规间隙
    val lg = 16.dp     // 卡片内边距、页面横向边距
    val xl = 20.dp     // 卡片间距、区块间距
    val xxl = 24.dp    // 大区块分隔
    val page = 16.dp   // 页面统一横向边距（全 App 一致）
}

/** 圆角分级（dp）。 */
object AppRadius {
    val chip = 50.dp        // 胶囊/标签（全圆角）
    val small = 10.dp       // 小元件（图标容器、输入框、chip 卡）
    val card = 16.dp        // 标准卡片
    val large = 20.dp       // 大容器/弹层/底部抽屉
    val iconBox = 11.dp     // 淡染图标方块容器（列表项左侧）
}

/** 圆角 Shape（直接用在 Modifier.clip / Surface.shape）。 */
object AppShape {
    val chip = RoundedCornerShape(AppRadius.chip)
    val small = RoundedCornerShape(AppRadius.small)
    val card = RoundedCornerShape(AppRadius.card)
    val large = RoundedCornerShape(AppRadius.large)
    val iconBox = RoundedCornerShape(AppRadius.iconBox)
    // 助手气泡：左上小、其余大（对话气泡专用）
    val bubble = RoundedCornerShape(topStart = 4.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 16.dp)
}

/** 动效令牌：统一时长（ms）+ 缓动曲线，全 App 动画引用同一套。 */
object AppMotion {
    const val fast = 150       // 微交互（按下、hover）
    const val normal = 220     // 常规（进场、切换）
    const val slow = 320       // 大转场

    // 标准缓动：略带减速，接近 iOS 的自然感（避免线性的机械感）
    val standardEasing = CubicBezierEasing(0.2f, 0f, 0f, 1f)
    val emphasizedEasing = CubicBezierEasing(0.2f, 0f, 0f, 1f)

    fun <T> tweenFast() = tween<T>(durationMillis = fast, easing = standardEasing)
    fun <T> tweenNormal() = tween<T>(durationMillis = normal, easing = standardEasing)
    fun <T> tweenSlow() = tween<T>(durationMillis = slow, easing = emphasizedEasing)
}

/** 组件尺寸常量（避免各页各写）。 */
object AppSize {
    val iconBox = 38.dp        // 列表项左侧淡染图标容器边长
    val iconInBox = 20.dp      // 容器内图标
    val touchTarget = 48.dp    // 最小可点区域（无障碍下限）
    val avatarSm = 32.dp
    val avatarMd = 44.dp
    val avatarLg = 60.dp
}
