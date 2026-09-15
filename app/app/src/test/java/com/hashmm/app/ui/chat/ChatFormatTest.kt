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

    @Test fun dayBeforeYesterday_showsQiantian() {
        val s = formatChatTime(isoDaysAgo(2))
        assertTrue("前天应含'前天'：$s", s.startsWith("前天"))
        assertTrue("前天应带时间：$s", s.contains("14:30"))
    }

    @Test fun within7days_showsWeekday() {
        val s = formatChatTime(isoDaysAgo(4))
        assertTrue("4 天前应显示星期：$s", s.startsWith("周"))
        assertTrue("应带时间：$s", s.contains("14:30"))
    }

    @Test fun beyond7days_showsFullYmd() {
        // 10 天前 → 一律 yyyy-MM-dd（带年），不再是 MM-dd，也不含"周/今天/昨天/前天"
        val s = formatChatTime(isoDaysAgo(10))
        assertTrue("≥7 天应为 yyyy-MM-dd：$s", Regex("""^\d{4}-\d{2}-\d{2}$""").matches(s))
    }

    @Test fun crossYear_showsFullDate() {
        // 明确造一个 2023-03-05 的固定日期 → 必然 ≥7 天，应为 yyyy-MM-dd
        val s = formatChatTime("2023-03-05T09:00:00+08:00")
        assertEquals("应显示完整年月日", "2023-03-05", s)
    }

    @Test fun fullTimestamp_hasDateAndTime() {
        // 点进/长按看的完整时间戳：yyyy-MM-dd HH:mm
        val s = formatChatTimeFull("2023-03-05T09:07:00Z")
        assertTrue("完整时间戳应含年月日与时分：$s", Regex("""^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$""").matches(s))
    }

    @Test fun blank_returnsEmpty() {
        assertEquals("", formatChatTime(""))
    }

    @Test fun garbage_safeFallback() {
        // V308 修恒真断言：原为 `assertTrue(s.isNotEmpty() || s.isEmpty())` —— 对【任何】
        // 字符串都成立，等于什么都没验证。这个用例真正要守的契约是"非法输入安全回退、
        // 不抛异常"，直接对此下断言：调用本身不抛，且返回值非 null（Kotlin 类型系统保证），
        // 并且不会把垃圾原样吐回去。
        val s = try {
            formatChatTime("not-a-date")
        } catch (e: Exception) {
            org.junit.Assert.fail("非法输入不应抛异常，实际抛出: $e")
            return
        }
        assertTrue("非法输入不应把原始垃圾串原样返回", s != "not-a-date")
    }
}
