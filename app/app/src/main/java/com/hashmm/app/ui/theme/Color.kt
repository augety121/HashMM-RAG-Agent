package com.hashmm.app.ui.theme

import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

// ════════════════════════════════════════════════════════════════
//  品牌配色 V2：与图标/吉祥物对齐——黑机身 #16161A + 红围巾 #EF3E36 + 白底。
//  强调色统一为品牌红 #EF3E36；深色主题用近黑（=吉祥物机身色）而非纯黑或 navy。
//  （旧的青绿 #0D9488 全部退役：与红黑图标互补色相冲，甲方明确要求换成贴合图标的色。）
//  说明：本文件只换"值"不换"名"——其它文件 import 的 Brand/Accent/AvatarGradient… 都不变，全局自动生效。
// ════════════════════════════════════════════════════════════════

// ════ V250 按 marvis-design-proposal 定死规范重做 ════
// 主色＝墨黑（主操作/文字/导航选中）；围巾红＝唯一强调色（只用在品牌/高亮/删除，不滥用）；
// 暖粉/米色承载"AI 语气"。改值不改名，全 App 生效。
val Brand = Color(0xFF1A1A1A)            // 墨黑 —— 主操作/主按钮/导航选中/主文字强调
val BrandDark = Color(0xFF0A0A0A)        // 纯黑（按下 / 深容器）
val BrandLight = Color(0xFFF4F4F5)       // 深色主题主色（近黑底上用浅色）
val BrandContainer = Color(0xFFEFEFF0)   // 浅灰容器（灰胶囊/输入框）
val Violet = Color(0xFF0A0A0A)           // 纯黑（渐变深端 / 兼容旧名保留）
val Accent = Color(0xFF1A1A1A)           // 强调色＝墨黑（主操作语义）
val AccentSoft = Color(0xFFEFEFF0)       // 浅灰容器

// 围巾红：唯一品牌强调色（品牌标识/删除/关键高亮）——只在这些地方用，不滥用
val BrandRed = Color(0xFFF5322D)         // 围巾红 —— 品牌/删除/高亮
val BrandRedDark = Color(0xFFD92B26)     // 深红（小字强调/按下）
val BrandRedSoft = Color(0xFFFBEAE6)     // 暖粉容器（承载"AI 语气"）
val WarmBeige = Color(0xFFF6ECE7)        // 米色容器（工具调用语气）
val OnWarmBeige = Color(0xFF9A6B4F)      // 米色容器上的暖棕文字（工具 chip 同款语气，V243）

// 渐变：墨黑（logo 块、头像、主 CTA）——高级黑
val BrandGradient = Brush.linearGradient(listOf(Color(0xFF2A2A2A), Color(0xFF1A1A1A)))
val AvatarGradient = Brush.linearGradient(listOf(Color(0xFF2A2A2A), Color(0xFF0A0A0A)))

// 浅色系：白底 + 极浅中性灰；深色文字用近黑（=吉祥物机身 #16161A），不用纯黑
val LightBg = Color(0xFFF4F4F5)          // 页面底（方案定：#F4F4F5）
val LightSurface = Color(0xFFFFFFFF)     // 卡片白
val LightSurfaceVariant = Color(0xFFEFEFF0) // 灰胶囊/输入（方案定：#EFEFF0）
val LightOnSurface = Color(0xFF1A1A1A)   // 主文字：墨黑（方案定：#1A1A1A）
val LightOnSurfaceVariant = Color(0xFF8E8E93) // 次要文字：中性灰（方案定：#8E8E93）
val LightOutline = Color(0xFFE4E4E7)     // 边框
val LightOutlineVariant = Color(0xFFECECEC) // 分割线（方案定：#ECECEC）

// 深色系：近黑（吉祥物机身色族），与红强调色搭配，干净有品牌感
val DarkBg = Color(0xFF0F0F12)
val DarkSurface = Color(0xFF16161A)
val DarkSurfaceVariant = Color(0xFF1F1F24)
val DarkOnSurface = Color(0xFFF2F2F4)
val DarkOnSurfaceVariant = Color(0xFF9A9AA4)
val DarkOutline = Color(0xFF2C2C33)
val DarkOutlineVariant = Color(0xFF232329)

// ── V201 surfaceContainer 家族（菜单/底部抽屉/对话框的容器色）──
// M3 不覆盖时用带紫调的基线中性色（DropdownMenu/BottomSheet 发紫的根因），
// 这里统一收进品牌中性灰族，浮层与页面同一套色，全 App 一次生效。
val LightContainerLowest = Color(0xFFFFFFFF)
val LightContainerLow = Color(0xFFFBFBFC)
val LightContainer = Color(0xFFF5F5F7)
val LightContainerHigh = Color(0xFFF0F0F2)
val LightContainerHighest = Color(0xFFEAEAEC)
val DarkContainerLowest = Color(0xFF0C0C0F)
val DarkContainerLow = Color(0xFF141417)
val DarkContainer = Color(0xFF1A1A1F)
val DarkContainerHigh = Color(0xFF1F1F24)
val DarkContainerHighest = Color(0xFF26262C)
