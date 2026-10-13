package com.hashmm.app.data.sync

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** 与 Supabase chat_conversations 表一一对应（镜像客户端 conversations 表）。 */
@Serializable
data class ChatConversation(
    val id: String,
    @SerialName("user_id") val userId: String = "",
    val title: String = "新对话",
    val pinned: Boolean = false,
    val metadata: JsonElement? = null,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
    @SerialName("last_message_at") val lastMessageAt: String = "",
)

/** 与 Supabase chat_messages 表一一对应（镜像客户端 messages 表）。 */
@Serializable
data class ChatMessage(
    val id: String,
    @SerialName("conv_id") val convId: String = "",
    @SerialName("user_id") val userId: String = "",
    val role: String = "user",
    val content: String = "",
    val thinking: String = "",
    @SerialName("tool_calls") val toolCalls: JsonElement? = null,
    val files: JsonElement? = null,
    val sources: JsonElement? = null,
    val groundings: JsonElement? = null,
    @SerialName("run_manifest") val runManifest: JsonElement? = null,
    val suggestions: JsonElement? = null,
    val status: String = "complete",
    val feedback: String = "",
    @SerialName("tokens_in") val tokensIn: Int = 0,
    @SerialName("tokens_out") val tokensOut: Int = 0,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
)

/** 与 Supabase user_memory 表一一对应。 */
@Serializable
data class UserMemory(
    val id: String,
    @SerialName("user_id") val userId: String = "",
    val category: String = "",
    val key: String = "",
    val value: String = "",
    val confidence: Double = 0.8,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("last_used") val lastUsed: String = "",
)

/** 与 Supabase user_settings 表一一对应（profile 为整块设置 JSON）。 */
@Serializable
data class UserSettings(
    @SerialName("user_id") val userId: String = "",
    val profile: JsonElement? = null,
    @SerialName("updated_at") val updatedAt: String = "",
)
