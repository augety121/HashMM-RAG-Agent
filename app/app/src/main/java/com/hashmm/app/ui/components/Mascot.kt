package com.hashmm.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke

/**
 * HashMM 吉祥物「小哈」—— 纯 Canvas 绘制的友好助手形象。
 * 近黑机器人头 + 天线红点 + 白眼睛 + 红围巾，黑白红极简（不照搬 Marvis 的马，原创形象）。
 */
@Composable
fun HashMascot(modifier: Modifier = Modifier) {
    val ink = Color(0xFF16161A)
    val white = Color.White
    val red = Color(0xFFEF3E36)
    Canvas(modifier) {
        val w = size.width
        val h = size.height

        // 头（圆角方）
        val head = w * 0.60f
        val hx = (w - head) / 2f
        val hy = h * 0.16f
        drawRoundRect(
            color = ink,
            topLeft = Offset(hx, hy),
            size = Size(head, head),
            cornerRadius = CornerRadius(head * 0.34f, head * 0.34f),
        )

        // 天线 + 红点
        drawLine(
            color = ink,
            start = Offset(w / 2f, hy),
            end = Offset(w / 2f, hy - h * 0.07f),
            strokeWidth = w * 0.028f,
            cap = StrokeCap.Round,
        )
        drawCircle(color = red, radius = w * 0.05f, center = Offset(w / 2f, hy - h * 0.085f))

        // 眼睛（白底 + 黑瞳）
        val eyeY = hy + head * 0.44f
        val eyeR = head * 0.115f
        val eL = Offset(hx + head * 0.33f, eyeY)
        val eR = Offset(hx + head * 0.67f, eyeY)
        drawCircle(white, eyeR, eL)
        drawCircle(white, eyeR, eR)
        drawCircle(ink, eyeR * 0.52f, Offset(eL.x + eyeR * 0.12f, eL.y + eyeR * 0.08f))
        drawCircle(ink, eyeR * 0.52f, Offset(eR.x + eyeR * 0.12f, eR.y + eyeR * 0.08f))
        // 高光
        drawCircle(white, eyeR * 0.16f, Offset(eL.x + eyeR * 0.34f, eL.y - eyeR * 0.18f))
        drawCircle(white, eyeR * 0.16f, Offset(eR.x + eyeR * 0.34f, eR.y - eyeR * 0.18f))

        // 腮红（淡红）
        drawCircle(red.copy(alpha = 0.35f), head * 0.07f, Offset(hx + head * 0.20f, eyeY + head * 0.16f))
        drawCircle(red.copy(alpha = 0.35f), head * 0.07f, Offset(hx + head * 0.80f, eyeY + head * 0.16f))

        // 红围巾（脖子横条 + 飘带）
        val sTop = hy + head * 0.98f
        drawRoundRect(
            color = red,
            topLeft = Offset(hx + head * 0.06f, sTop),
            size = Size(head * 0.88f, h * 0.11f),
            cornerRadius = CornerRadius(h * 0.035f, h * 0.035f),
        )
        drawRoundRect(
            color = red,
            topLeft = Offset(hx + head * 0.60f, sTop + h * 0.05f),
            size = Size(head * 0.20f, h * 0.16f),
            cornerRadius = CornerRadius(h * 0.025f, h * 0.025f),
        )
    }
}

/**
 * HashMM 首页 hero 版吉祥物「小哈」—— 在原 Mascot 基础上做氛围增强：
 * 双层柔光（红 + 中性径向渐变）+ 极淡轨道环 + 环上品牌点 + 地面投影 + 小哈本体。
 * 用途：首页空态主视觉，解决"一个小图标太单调、太小"的问题。纯 Canvas，无文字。
 * 普通 logo 位（侧栏/头像）继续用轻量的 HashMascot，本组件只用于首页。
 */
@Composable
fun HashMascotHero(modifier: Modifier = Modifier) {
    val ink = Color(0xFF16161A)
    val white = Color.White
    val red = Color(0xFFEF3E36)
    Canvas(modifier) {
        val w = size.width
        val h = size.height
        val cx = w / 2f
        val cy = h * 0.44f

        // 地面投影
        drawOval(
            color = ink.copy(alpha = 0.10f),
            topLeft = Offset(cx - w * 0.23f, h * 0.80f),
            size = Size(w * 0.46f, h * 0.075f),
        )

        // 双层柔光（中性 + 暖红径向渐变）
        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(ink.copy(alpha = 0.10f), ink.copy(alpha = 0f)),
                center = Offset(cx, cy), radius = w * 0.46f,
            ),
            radius = w * 0.46f, center = Offset(cx, cy),
        )
        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(red.copy(alpha = 0.18f), red.copy(alpha = 0f)),
                center = Offset(cx, cy), radius = w * 0.40f,
            ),
            radius = w * 0.40f, center = Offset(cx, cy),
        )

        // 极淡轨道环 + 环上四个品牌点
        val ringR = w * 0.40f
        drawCircle(color = ink.copy(alpha = 0.07f), radius = ringR, center = Offset(cx, cy), style = Stroke(width = w * 0.006f))
        drawCircle(color = red, radius = w * 0.018f, center = Offset(cx, cy - ringR))
        drawCircle(color = ink.copy(alpha = 0.5f), radius = w * 0.013f, center = Offset(cx + ringR, cy))
        drawCircle(color = red.copy(alpha = 0.7f), radius = w * 0.012f, center = Offset(cx, cy + ringR))
        drawCircle(color = ink.copy(alpha = 0.45f), radius = w * 0.013f, center = Offset(cx - ringR, cy))

        // —— 小哈本体（以 head 边长 hd 为单位，等比例还原原 Mascot 造型）——
        val hd = w * 0.40f
        val hx = cx - hd / 2f
        val hy = cy - hd * 0.52f

        // 天线 + 红点
        drawLine(
            color = ink, start = Offset(cx, hy), end = Offset(cx, hy - hd * 0.16f),
            strokeWidth = hd * 0.05f, cap = StrokeCap.Round,
        )
        drawCircle(color = red, radius = hd * 0.085f, center = Offset(cx, hy - hd * 0.19f))

        // 头（圆角方）
        drawRoundRect(
            color = ink, topLeft = Offset(hx, hy), size = Size(hd, hd),
            cornerRadius = CornerRadius(hd * 0.34f, hd * 0.34f),
        )

        // 腮红
        val eyeY = hy + hd * 0.44f
        drawCircle(red.copy(alpha = 0.35f), hd * 0.07f, Offset(hx + hd * 0.20f, eyeY + hd * 0.16f))
        drawCircle(red.copy(alpha = 0.35f), hd * 0.07f, Offset(hx + hd * 0.80f, eyeY + hd * 0.16f))

        // 眼睛（白底 + 黑瞳 + 高光）
        val eyeR = hd * 0.115f
        val eL = Offset(hx + hd * 0.33f, eyeY)
        val eR = Offset(hx + hd * 0.67f, eyeY)
        drawCircle(white, eyeR, eL)
        drawCircle(white, eyeR, eR)
        drawCircle(ink, eyeR * 0.52f, Offset(eL.x + eyeR * 0.12f, eL.y + eyeR * 0.08f))
        drawCircle(ink, eyeR * 0.52f, Offset(eR.x + eyeR * 0.12f, eR.y + eyeR * 0.08f))
        drawCircle(white, eyeR * 0.16f, Offset(eL.x + eyeR * 0.34f, eL.y - eyeR * 0.18f))
        drawCircle(white, eyeR * 0.16f, Offset(eR.x + eyeR * 0.34f, eR.y - eyeR * 0.18f))

        // 围巾 + 飘带
        val sTop = hy + hd * 0.98f
        drawRoundRect(
            color = red, topLeft = Offset(hx + hd * 0.06f, sTop),
            size = Size(hd * 0.88f, hd * 0.20f), cornerRadius = CornerRadius(hd * 0.06f, hd * 0.06f),
        )
        drawRoundRect(
            color = red, topLeft = Offset(hx + hd * 0.60f, sTop + hd * 0.10f),
            size = Size(hd * 0.20f, hd * 0.30f), cornerRadius = CornerRadius(hd * 0.05f, hd * 0.05f),
        )
    }
}
