package com.hashmm.app.ui.components

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import kotlin.math.min

private val ObserverInk = Color(0xFF18191C)
private val ObserverRed = Color(0xFFEF4148)

/** Observer V4 production mark. Coordinates match the desktop vector master. */
private fun DrawScope.drawObserverV4(boxSize: Float, center: Offset) {
    val scale = boxSize / 64f
    fun point(x: Float, y: Float) = Offset(
        center.x + (x - 32f) * scale,
        center.y + (y - 32f) * scale,
    )

    drawLine(
        color = ObserverRed,
        start = point(32f, 17f),
        end = point(32f, 11.75f),
        strokeWidth = 2f * scale,
        cap = StrokeCap.Round,
    )
    drawCircle(ObserverRed, 1.75f * scale, point(32f, 9.75f))

    val head = Path().apply {
        moveTo(point(32f, 16.75f).x, point(32f, 16.75f).y)
        cubicTo(
            point(22f, 16.75f).x, point(22f, 16.75f).y,
            point(16.75f, 21.25f).x, point(16.75f, 21.25f).y,
            point(16.75f, 31f).x, point(16.75f, 31f).y,
        )
        cubicTo(
            point(16.75f, 40.75f).x, point(16.75f, 40.75f).y,
            point(22f, 45.25f).x, point(22f, 45.25f).y,
            point(32f, 45.25f).x, point(32f, 45.25f).y,
        )
        cubicTo(
            point(42f, 45.25f).x, point(42f, 45.25f).y,
            point(47.25f, 40.75f).x, point(47.25f, 40.75f).y,
            point(47.25f, 31f).x, point(47.25f, 31f).y,
        )
        cubicTo(
            point(47.25f, 21.25f).x, point(47.25f, 21.25f).y,
            point(42f, 16.75f).x, point(42f, 16.75f).y,
            point(32f, 16.75f).x, point(32f, 16.75f).y,
        )
        close()
    }
    drawPath(head, ObserverInk)

    drawOval(Color.White, point(22f, 27.25f), Size(8.5f * scale, 7.5f * scale))
    drawOval(Color.White, point(34f, 26.5f), Size(9f * scale, 8.5f * scale))
    drawCircle(ObserverInk, 1.375f * scale, point(27.5f, 30.5f))
    drawCircle(ObserverInk, 1.375f * scale, point(39.75f, 30.25f))
    drawCircle(Color.White, .45f * scale, point(28f, 30f))
    drawCircle(Color.White, .45f * scale, point(40.25f, 29.75f))

    val tail = Path().apply {
        moveTo(point(32f, 50.25f).x, point(32f, 50.25f).y)
        lineTo(point(35.75f, 50.25f).x, point(35.75f, 50.25f).y)
        lineTo(point(35.75f, 54.25f).x, point(35.75f, 54.25f).y)
        cubicTo(
            point(35.75f, 55.75f).x, point(35.75f, 55.75f).y,
            point(35f, 57f).x, point(35f, 57f).y,
            point(33.75f, 57.75f).x, point(33.75f, 57.75f).y,
        )
        cubicTo(
            point(32.5f, 57f).x, point(32.5f, 57f).y,
            point(32f, 56f).x, point(32f, 56f).y,
            point(32f, 54.5f).x, point(32f, 54.5f).y,
        )
        close()
    }
    drawPath(tail, ObserverRed)
    drawRoundRect(
        color = ObserverRed,
        topLeft = point(18.25f, 47f),
        size = Size(27.5f * scale, 4.5f * scale),
        cornerRadius = CornerRadius(2.25f * scale),
    )
}

@Composable
fun HashMascot(modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val side = min(size.width, size.height)
        drawObserverV4(side, center)
    }
}

/** Home-only Observer V4 presentation with restrained ambient motion. */
@Composable
fun HashMascotHero(modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "observerV4Hero")
    val breathe by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(4500, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "breathe",
    )
    val spin by transition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(
            animation = tween(26000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "spin",
    )

    Canvas(modifier) {
        val side = min(size.width, size.height)
        val c = center
        val glowRadius = side * (0.44f + breathe * 0.02f)
        drawOval(
            color = ObserverInk.copy(alpha = .09f),
            topLeft = Offset(c.x - side * .22f, size.height * .82f),
            size = Size(side * .44f, side * .07f),
        )
        drawCircle(
            brush = Brush.radialGradient(
                listOf(ObserverRed.copy(alpha = .14f), Color.Transparent),
                center = c,
                radius = glowRadius,
            ),
            radius = glowRadius,
            center = c,
        )
        rotate(spin, c) {
            val ring = side * .40f
            drawCircle(ObserverInk.copy(alpha = .07f), ring, c, style = Stroke(side * .005f))
            drawCircle(ObserverRed, side * .014f, Offset(c.x, c.y - ring))
            drawCircle(ObserverInk.copy(alpha = .45f), side * .010f, Offset(c.x + ring, c.y))
            drawCircle(ObserverRed.copy(alpha = .70f), side * .010f, Offset(c.x, c.y + ring))
        }
        drawObserverV4(side * .78f, c)
    }
}
