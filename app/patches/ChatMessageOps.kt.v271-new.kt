package com.hashmm.app.ui.chat

import com.hashmm.app.data.sync.ChatMessage

/**
 * V270 聊天消息列表整理——App 专属的消息重构逻辑（不是后端/桌面端的东西）。
 *
 * App 的消息流是"乐观本地占位 → 云端/后端规范列表覆盖"的两段式：发送时先塞一条本地气泡，随后
 * 拉到服务端规范列表再整体替换。这中间很容易出现"同一条消息相邻重复"（本地占位 + 规范返回同内容）。
 * 这里把整理逻辑抽成**纯函数**，既给 ChatDetailViewModel 复用，又能被 `./gradlew test` 直接单测——
 * 之前它是 ViewModel 的 private 方法，测不到。
 */
object ChatMessageOps {

    /**
     * 合并相邻的「同角色 + 同内容」消息（内容非空才去重）——消除乐观占位与规范返回撞车产生的重影。
     * 保持原始顺序；空内容（如流式刚建的占位）一律保留，不误删。
     */
    fun dedupeAdjacent(list: List<ChatMessage>): List<ChatMessage> {
        if (list.size < 2) return list
        val out = ArrayList<ChatMessage>(list.size)
        for (m in list) {
            val last = out.lastOrNull()
            if (last != null && last.role == m.role && last.content == m.content && m.content.isNotBlank()) continue
            out.add(m)
        }
        return out
    }

    /**
     * 是否存在仍在生成中的消息（status=="streaming"）——App 的"重连轮询是否该继续"判据。
     * 抽出来单测，保证判据稳定（这条链断了 App 会"一直转/一直空白"）。
     */
    fun hasStreaming(list: List<ChatMessage>): Boolean = list.any { it.status == "streaming" }

    /**
     * 用服务端规范列表覆盖本地，但**保留仍在生成的本地尾巴**：当服务端还没落库那条 streaming 占位
     * 时，直接替换会让正在生成的气泡闪没。规则：若本地最后一条是 streaming 且服务端列表里没有同 id，
     * 则把它接在服务端列表尾部。
     */
    fun reconcile(local: List<ChatMessage>, server: List<ChatMessage>): List<ChatMessage> {
        if (server.isEmpty()) return local
        val tail = local.lastOrNull()
        if (tail != null && tail.status == "streaming" && server.none { it.id == tail.id }) {
            return dedupeAdjacent(server + tail)
        }
        return dedupeAdjacent(server)
    }
}
