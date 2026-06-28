package com.hashmm.app.ui.chat

import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val displayFmt: DateTimeFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")

/** 把 Supabase 的 ISO timestamptz 转成本地「MM-dd HH:mm」。解析失败则安全回退。 */
fun formatChatTime(iso: String): String {
    if (iso.isBlank()) return ""
    return try {
        OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault()).format(displayFmt)
    } catch (e: Exception) {
        iso.take(16).replace('T', ' ')
    }
}
