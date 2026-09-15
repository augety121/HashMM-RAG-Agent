package com.hashmm.app.ui.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * V269 会话列表时间显示单元测试（对齐诉求："几天内显示星期，超过 7 天显示完整年月日"）。
 * 纯 JVM（java.time），不需要设备/模拟器，`./gradlew test` 即可跑。
 */
class ChatFormatTest {

    private val iso = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssXXX")

    /** 造一个"距今 n 天、当天 14:30"的 ISO 时间戳（本地时区），喂给 formatChatTime。 */
    private fun isoDaysAgo(days: Long, hour: Int = 14, minute: Int = 30): String {
        val z = OffsetDateTime.now(ZoneId.systemDefault())
            .minusDays(days)
            .withHour(hour).withMinute(minute).withSecond(0).withNano(0)
        return z.format(iso)
    }

    @Test fun today_showsToday() {
        val s = formatChatTime(isoDaysAgo(0))
        assertTrue("今天应含'今天'：$s", s.startsWith("今天"))
        assertTrue("今天应含时间：$s", s.contains("14:30"))
    }

    @Test fun yesterday_showsYesterday() {
        val s = formatChatTime(isoDaysAgo(1))
        assertTrue("昨天应含'昨天'：$s", s.startsWith("昨天"))
    }

    @Test fun within7days_showsWeekday() {
        val s = formatChatTime(isoDaysAgo(3))
        assertTrue("3 天前应显示星期：$s", s.startsWith("周"))
        assertTrue("应带时间：$s", s.contains("14:30"))
    }

    @Test fun olderThisYear_showsMonthDay() {
        // 10 天前（仍在同年绝大多数情况下）→ MM-dd HH:mm，且不含"周/今天/昨天"
        val s = formatChatTime(isoDaysAgo(10))
        // 只有当 10 天前跨年时才会是 yyyy-MM-dd；否则必须是 MM-dd HH:mm
        val crossYear = OffsetDateTime.now(ZoneId.systemDefault()).minusDays(10).year !=
            OffsetDateTime.now(ZoneId.systemDefault()).year
        if (!crossYear) {
            assertTrue("同年 10 天前应为 MM-dd HH:mm：$s", s.contains("-") && s.contains("14:30"))
            assertTrue("不应再显示'今天/昨天/周'：$s", !s.startsWith("今天") && !s.startsWith("昨天") && !s.startsWith("周"))
        }
    }

    @Test fun crossYear_showsFullDate() {
        // 明确造一个 2023-03-05 的固定日期 → 必然跨年（当前是 2026），应为 yyyy-MM-dd
        val s = formatChatTime("2023-03-05T09:00:00+08:00")
        assertEquals("跨年应显示完整年月日", "2023-03-05", s)
    }

    @Test fun blank_returnsEmpty() {
        assertEquals("", formatChatTime(""))
    }

    @Test fun garbage_safeFallback() {
        // 非法字符串不该抛异常，安全回退
        val s = formatChatTime("not-a-date")
        assertTrue("非法输入安全回退不抛异常", s.isNotEmpty() || s.isEmpty())
    }
}
