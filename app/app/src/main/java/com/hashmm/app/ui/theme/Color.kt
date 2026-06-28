package com.hashmm.app.ui.theme

import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

// ════════════════════════════════════════════════════════════════
//  品牌配色 V2：与图标/吉祥物对齐——黑机身 #16161A + 红围巾 #EF3E36 + 白底。
//  强调色统一为品牌红 #EF3E36；深色主题用近黑（=吉祥物机身色）而非纯黑或 navy。
//  （旧的青绿 #0D9488 全部退役：与红黑图标互补色相冲，甲方明确要求换成贴合图标的色。）
//  说明：本文件只换"值"不换"名"——其它文件 import 的 Brand/Accent/AvatarGradient… 都不变，全局自动生效。
// ════════════════════════════════════════════════════════════════

// 主色：品牌红
val Brand = Color(0xFFEF3E36)            // 围巾红 —— 全 App 强调色
val BrandDark = Color(0xFFD32F2A)        // 深一档（按下 / 深容器 / 强调文字）
val BrandLight = Color(0xFFFF5247)       // 深色主题主色（亮一档，近黑底上更醒目）
val BrandContainer = Color(0xFFFFE2E0)   // 浅红容器（浅色主题 primaryContainer）
val Violet = Color(0xFF7A1A16)           // 深红（渐变深端 / 兼容旧名保留）
val Accent = Color(0xFFEF3E36)           // 强调色＝品牌红
val AccentSoft = Color(0xFFFFE2E0)       // 浅红容器

// 渐变：品牌红（logo 块、头像）
val BrandGradient = Brush.linearGradient(listOf(Color(0xFFFF5A4E), Color(0xFFEF3E36)))
val AvatarGradient = Brush.linearGradient(listOf(Color(0xFFFF5A4E), Color(0xFFD32F2A)))

// 浅色系：白底 + 极浅中性灰；深色文字用近黑（=吉祥物机身 #16161A），不用纯黑
val LightBg = Color(0xFFF7F7F8)
val LightSurface = Color(0xFFFFFFFF)
val LightSurfaceVariant = Color(0xFFF0F0F2)
val LightOnSurface = Color(0xFF16161A)
val LightOnSurfaceVariant = Color(0xFF5C5C66)
val LightOutline = Color(0xFFE5E5E8)
val LightOutlineVariant = Color(0xFFEEEEF0)

// 深色系：近黑（吉祥物机身色族），与红强调色搭配，干净有品牌感
val DarkBg = Color(0xFF0F0F12)
val DarkSurface = Color(0xFF16161A)
val DarkSurfaceVariant = Color(0xFF1F1F24)
val DarkOnSurface = Color(0xFFF2F2F4)
val DarkOnSurfaceVariant = Color(0xFF9A9AA4)
val DarkOutline = Color(0xFF2C2C33)
val DarkOutlineVariant = Color(0xFF232329)
