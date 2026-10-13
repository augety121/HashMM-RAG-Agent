package com.hashmm.app.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * App Room 数据库。V306：消息从"分页加密 JSON 文件"迁到 Room（SQLite）。
 *
 * 加密说明：如需静态加密，可接 SQLCipher（net.zetetic:android-database-sqlcipher）+
 * SupportFactory，用 SecureKeyStore 里的随机密钥打开库。默认先用明文 Room（会话内容敏感度
 * 由业务判断）；接 SQLCipher 只需在 build 时传 openHelperFactory，不改 DAO/实体。
 */
@Database(entities = [MessageEntity::class], version = 1, exportSchema = true)
abstract class AppDatabase : RoomDatabase() {
    abstract fun messageDao(): MessageDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext, AppDatabase::class.java, "hashmm.db")
                    .fallbackToDestructiveMigration(false)
                    .build().also { INSTANCE = it }
            }
    }
}
