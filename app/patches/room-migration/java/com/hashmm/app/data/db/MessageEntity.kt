package com.hashmm.app.data.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Room 消息实体（V306 Room 迁移，彻底替代分页 JSON 存储 → 真数据库，解决 IO 抖动）。
 *
 * 设计要点：
 *  · 主键 id（消息 id）；(convId, createdAt) 建索引 → 按会话取消息、按时间排序都走索引，O(log n)；
 *  · 追加/更新单条只写一行，不再整会话重序列化（旧分页方案的 O(页) 降到 O(1)）；
 *  · 复杂字段（toolCalls/files/sources/suggestions）以 JSON 字符串落列，读出时再解析——
 *    与 SyncModels.ChatMessage 的 JsonElement 一一对应，避免 schema 爆炸。
 */
@Entity(
    tableName = "messages",
    indices = [Index(value = ["convId", "createdAt"]), Index(value = ["convId"])],
)
data class MessageEntity(
    @PrimaryKey val id: String,
    val convId: String,
    val userId: String = "",
    val role: String = "user",
    val content: String = "",
    val thinking: String = "",
    val toolCallsJson: String? = null,
    val filesJson: String? = null,
    val sourcesJson: String? = null,
    val suggestionsJson: String? = null,
    val status: String = "complete",
    val tokensIn: Int = 0,
    val tokensOut: Int = 0,
    val createdAt: String = "",
    val updatedAt: String = "",
    // 排序键：createdAt 解析成毫秒（写入时算一次），排序不再逐行解析 ISO
    val sortKey: Long = 0L,
)
