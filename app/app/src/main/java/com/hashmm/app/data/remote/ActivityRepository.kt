package com.hashmm.app.data.remote

/** 客户端正在生成回复的某个对话。 */
data class ActiveChat(
    val convId: String,
    val title: String,
    val status: String = "active",
)

/** 客户端的后台任务（如 RAG 解析/索引）。 */
data class JobInfo(
    val id: String,
    val kind: String,
    val status: String,
    val done: Int,
    val total: Int,
    val message: String,
)

/** 客户端当前动态：进行中的对话 + 后台任务。由实时层(ActivityRealtimeRepository)填充。 */
data class Activity(
    val activeChats: List<ActiveChat> = emptyList(),
    val jobs: List<JobInfo> = emptyList(),
)
