package com.hashmm.app.data.db

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * V306 Room MessageDao 仪器测试(androidTest,需设备/模拟器 + Room 运行时;`./gradlew connectedAndroidTest`)。
 * 用内存库验证:并发 upsert 不丢、分页边界、replaceConv 事务性、排序走 sortKey。
 * ——补上"把分页 JSON 换成真数据库"后对 DAO 的真实回归。
 */
@RunWith(AndroidJUnit4::class)
class MessageDaoTest {

    private lateinit var db: AppDatabase
    private lateinit var dao: MessageDao

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(), AppDatabase::class.java,
        ).build()
        dao = db.messageDao()
    }

    @After fun tearDown() = db.close()

    private fun ent(id: String, conv: String, sortKey: Long, content: String = "c") =
        MessageEntity(id = id, convId = conv, content = content, sortKey = sortKey, createdAt = id)

    @Test fun upsert_and_read_orderedBySortKey() = runBlocking {
        dao.upsert(ent("m3", "c1", 300))
        dao.upsert(ent("m1", "c1", 100))
        dao.upsert(ent("m2", "c1", 200))
        val out = dao.messagesOf("c1")
        assertEquals(listOf("m1", "m2", "m3"), out.map { it.id })
    }

    @Test fun upsert_replacesSameId() = runBlocking {
        dao.upsert(ent("m1", "c1", 100, "old"))
        dao.upsert(ent("m1", "c1", 100, "new"))
        assertEquals(1, dao.countOf("c1"))
        assertEquals("new", dao.messagesOf("c1").first().content)
    }

    @Test fun concurrentUpsert_noLoss() = runBlocking {
        val n = 200
        (0 until n).map { i -> async { dao.upsert(ent("m$i", "c1", i.toLong())) } }.awaitAll()
        assertEquals(n, dao.countOf("c1"))
    }

    @Test fun pagination_boundaries() = runBlocking {
        (0 until 50).forEach { i -> dao.upsert(ent("m%03d".format(i), "c1", i.toLong())) }
        val page0 = dao.messagesPage("c1", limit = 20, offset = 0)
        val page2 = dao.messagesPage("c1", limit = 20, offset = 40)
        assertEquals(20, page0.size)
        assertEquals(10, page2.size)   // 50 - 40
        assertEquals("m000", page0.first().id)
        assertEquals("m049", page2.last().id)
    }

    @Test fun replaceConv_isTransactionalSwap() = runBlocking {
        (0 until 5).forEach { i -> dao.upsert(ent("old$i", "c1", i.toLong())) }
        val fresh = (0 until 3).map { i -> ent("new$i", "c1", (i + 100).toLong()) }
        dao.replaceConv("c1", fresh)
        val out = dao.messagesOf("c1")
        assertEquals(3, out.size)
        assertTrue("旧消息应被清空", out.none { it.id.startsWith("old") })
        assertEquals(listOf("new0", "new1", "new2"), out.map { it.id })
    }

    @Test fun deleteConv_onlyAffectsThatConv() = runBlocking {
        dao.upsert(ent("a", "c1", 1))
        dao.upsert(ent("b", "c2", 1))
        dao.deleteConv("c1")
        assertEquals(0, dao.countOf("c1"))
        assertEquals(1, dao.countOf("c2"))
    }
}
