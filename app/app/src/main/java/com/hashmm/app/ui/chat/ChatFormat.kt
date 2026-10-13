package com.hashmm.app.ui.chat

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

private val hm: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")
private val ymd: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")
private val ymdHm: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
private val weekdayCn = arrayOf("周一", "周二", "周三", "周四", "周五", "周六", "周日")

/** 把 ISO timestamptz 解析成本地时区的 ZonedDateTime；失败返回 null。App 时间显示的统一入口。 */
private fun parseLocal(iso: String) = try {
    val raw = iso.trim()
    val numeric = raw.toDoubleOrNull()
    if (numeric != null) {
        val millis = if (kotlin.math.abs(numeric) < 1_000_000_000_000.0) (numeric * 1000.0).toLong() else numeric.toLong()
        Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault())
    } else {
        val normalized = raw.replace(' ', 'T').let { value ->
            Regex("([+-])([0-9]{2})$").replace(value) { m -> "${m.groupValues[1]}${m.groupValues[2]}:00" }
        }
        runCatching { OffsetDateTime.parse(raw).atZoneSameInstant(ZoneId.systemDefault()) }.getOrElse {
            runCatching { OffsetDateTime.parse(normalized).atZoneSameInstant(ZoneId.systemDefault()) }.getOrElse {
                runCatching { Instant.parse(raw).atZone(ZoneId.systemDefault()) }.getOrElse {
                    runCatching { LocalDateTime.parse(normalized).atZone(ZoneId.systemDefault()) }.getOrElse {
                        LocalDate.parse(raw).atStartOfDay(ZoneId.systemDefault())
                    }
                }
            }
        }
    }
} catch (_: Exception) {
    null
}

/**
 * V272 会话列表时间显示（严格按诉求）：
 *   今天    → 今天 HH:mm
 *   昨天    → 昨天 HH:mm
 *   前天    → 前天 HH:mm
 *   3–6 天  → 周三 HH:mm（星期几）
 *   ≥7 天   → yyyy-MM-dd（年月日，带年）
 * 之前无论多早一律 "MM-dd HH:mm"，用户反馈"看不出是哪天/像都在今天/没有前天"。解析失败安全回退。
 */
fun formatChatTime(iso: String): String {
    if (iso.isBlank()) return ""
    // Never surface an untrusted timestamp verbatim in the UI. A malformed
    // server value is not evidence of a real date, so keep the display empty.
    val zoned = parseLocal(iso) ?: return ""
    val date = zoned.toLocalDate()
    val today = LocalDate.now(ZoneId.systemDefault())
    val diff = ChronoUnit.DAYS.between(date, today)   // 今天=0，昨天=1，前天=2…
    return when {
        diff <= 0L -> "今天 " + zoned.format(hm)
        diff == 1L -> "昨天 " + zoned.format(hm)
        diff == 2L -> "前天 " + zoned.format(hm)
        diff < 7L -> weekdayCn[date.dayOfWeek.value - 1] + " " + zoned.format(hm)
        else -> zoned.format(ymd)   // ≥7 天：完整年月日
    }
}

/** 点进会话/长按查看的完整时间戳：yyyy-MM-dd HH:mm（"点击可以看到年月日"）。解析失败回退原串。 */
fun formatChatTimeFull(iso: String): String {
    if (iso.isBlank()) return ""
    val zoned = parseLocal(iso) ?: return ""
    return zoned.format(ymdHm)
}
