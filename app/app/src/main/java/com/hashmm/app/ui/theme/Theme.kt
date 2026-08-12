package com.hashmm.app.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val LightColors = lightColorScheme(
    primary = Brand,               // 墨黑：主操作/按钮/导航
    onPrimary = Color.White,
    primaryContainer = BrandContainer,
    onPrimaryContainer = Brand,
    secondary = BrandRed,          // 围巾红：品牌强调（唯一强调色）
    onSecondary = Color.White,
    secondaryContainer = BrandRedSoft,   // 暖粉容器
    onSecondaryContainer = BrandRedDark,
    tertiary = BrandRed,           // 高亮也用围巾红
    onTertiary = Color.White,
    error = BrandRed,              // 删除/退出＝围巾红（方案定）
    onError = Color.White,
    background = LightBg,
    onBackground = LightOnSurface,
    surface = LightSurface,
    onSurface = LightOnSurface,
    surfaceVariant = LightSurfaceVariant,
    onSurfaceVariant = LightOnSurfaceVariant,
    outline = LightOutline,
    outlineVariant = LightOutlineVariant,
    // 浮层容器族：菜单/底部抽屉/对话框统一走品牌中性灰（去掉 M3 基线紫调）
    surfaceContainerLowest = LightContainerLowest,
    surfaceContainerLow = LightContainerLow,
    surfaceContainer = LightContainer,
    surfaceContainerHigh = LightContainerHigh,
    surfaceContainerHighest = LightContainerHighest,
    surfaceTint = LightSurface,   // 关掉色调叠加：高程不再染色，浮层永远干净
)

private val DarkColors = darkColorScheme(
    primary = BrandLight,
    onPrimary = Color(0xFF1A1A1A),   // V247: 深色主题主色是浅灰(BrandLight)，其上文字须用黑（白字会看不见）
    primaryContainer = BrandDark,
    onPrimaryContainer = Color.White,
    secondary = BrandLight,
    onSecondary = Color(0xFF1A1A1A),
    background = DarkBg,
    onBackground = DarkOnSurface,
    surface = DarkSurface,
    onSurface = DarkOnSurface,
    surfaceVariant = DarkSurfaceVariant,
    onSurfaceVariant = DarkOnSurfaceVariant,
    outline = DarkOutline,
    outlineVariant = DarkOutlineVariant,
    surfaceContainerLowest = DarkContainerLowest,
    surfaceContainerLow = DarkContainerLow,
    surfaceContainer = DarkContainer,
    surfaceContainerHigh = DarkContainerHigh,
    surfaceContainerHighest = DarkContainerHighest,
    surfaceTint = DarkSurface,
)

/**
 * 默认关闭 dynamicColor —— 用品牌红 #EF3E36 + 近黑/白中性色（贴合图标），保证全 App 视觉一致、有品牌感。
 * （Material You 动态取色会跟随壁纸，导致观感漂移；这里以一致性 + 品牌识别优先。）
 */
@Composable
fun HashMMTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit,
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val ctx = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        }
        darkTheme -> DarkColors
        else -> LightColors
    }
    MaterialTheme(colorScheme = colorScheme, typography = Typography, content = content)
}
