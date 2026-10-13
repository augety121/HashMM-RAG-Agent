package com.hashmm.app.di

import com.hashmm.app.BuildConfig
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.Auth
import io.github.jan.supabase.createSupabaseClient
import io.github.jan.supabase.postgrest.Postgrest
import io.github.jan.supabase.realtime.Realtime
import javax.inject.Singleton

/**
 * 全局依赖。Supabase 装两个插件：
 *   · Auth     —— 登录鉴权（与桌面客户端同账号）
 *   · Postgrest —— 读取云端同步的会话 / 设置 / 记忆（chat_conversations / user_settings / user_memory）
 * RAG 大文件 / 向量不进 Supabase，由 AutoDL 私有云直连，故不装 Storage。
 */
@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun provideSupabaseClient(): SupabaseClient = createSupabaseClient(
        supabaseUrl = BuildConfig.SUPABASE_URL,
        supabaseKey = BuildConfig.SUPABASE_KEY,
    ) {
        install(Auth) {
            autoLoadFromStorage = true   // 登录态自动持久化，进程重启免重登
            alwaysAutoRefresh = true     // JWT 过期前自动刷新
        }
        install(Postgrest)               // 云端同步数据读取
        install(Realtime)                // 任务进度实时推送
    }

    /**
     * V308：LiveStreamSource 绑定到 ChatLiveRepository。
     * LiveChatManager 现在依赖【接口】而非具体类 —— 生产走真实 Repository，单测注入 Fake。
     * （此前 LiveChatManager 直接依赖具体类，导致单测无法注入依赖、只能测 StringBuilder，
     *   两个真实并发竞态长期无人发现。）
     */
    @Provides
    @Singleton
    fun provideLiveStreamSource(
        repo: com.hashmm.app.data.remote.ChatLiveRepository,
    ): com.hashmm.app.data.remote.LiveStreamSource = repo
}
