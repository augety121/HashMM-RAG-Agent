package com.hashmm.app.data.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

/**
 * 消息 DAO。追加/更新单条 = 单行写（O(1)）；分页读走 LIMIT/OFFSET + 索引；
 * 提供 Flow 版本供 UI 响应式订阅（新消息自动刷新，无需整表重读）。
 */
@Dao
interface MessageDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(msg: MessageEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(msgs: List<MessageEntity>)

    /** 取某会话全部消息（按时间升序，走 (convId, createdAt) 索引）。 */
    @Query("SELECT * FROM messages WHERE convId = :convId ORDER BY sortKey ASC, createdAt ASC")
    suspend fun messagesOf(convId: String): List<MessageEntity>

    /** 分页取（长会话只读一页，不整表加载）。 */
    @Query("SELECT * FROM messages WHERE convId = :convId ORDER BY sortKey ASC, createdAt ASC LIMIT :limit OFFSET :offset")
    suspend fun messagesPage(convId: String, limit: Int, offset: Int): List<MessageEntity>

    /** 响应式订阅某会话消息（新消息落库后 UI 自动更新）。 */
    @Query("SELECT * FROM messages WHERE convId = :convId ORDER BY sortKey ASC, createdAt ASC")
    fun messagesFlow(convId: String): Flow<List<MessageEntity>>

    @Query("SELECT COUNT(*) FROM messages WHERE convId = :convId")
    suspend fun countOf(convId: String): Int

    @Query("DELETE FROM messages WHERE convId = :convId")
    suspend fun deleteConv(convId: String)

    @Query("DELETE FROM messages")
    suspend fun clearAll()

    /** 用服务端最新列表覆盖某会话：整会话替换（删旧插新）在一个事务里完成。 */
    @Transaction
    suspend fun replaceConv(convId: String, msgs: List<MessageEntity>) {
        deleteConv(convId)
        upsertAll(msgs)
    }
}
