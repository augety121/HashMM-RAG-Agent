package com.hashmm.app.ui

/** 导航路由常量。主框架是底部 Tab；详情/登录/远程为压栈页面。 */
object Routes {
    const val MAIN = "main"                       // 底部 Tab 主框架
    const val REMOTE = "remote"                   // 远程控制（从「我的」进入）
    const val TERMS = "terms"                     // 用户协议
    const val PRIVACY = "privacy"                 // 隐私政策
    const val MEMORY = "memory"                   // 记忆中心（云端同步）
    const val KNOWLEDGE = "knowledge"             // 知识库（直连客户端后端）
    const val WEB_WORKBENCH = "web_workbench"     // 网页版完整客户端（WebView 兜底）
    const val MODELS = "models"                   // 模型/后端配置
    const val KG = "kg"                           // 知识图谱
    const val ADMIN = "admin"                     // 管理后台
    const val RELAY = "relay"                     // 接力（设备交接）
    const val USAGE = "usage"                     // 用量
    const val VALIDITY = "validity"               // 失效区（文档时效/归档）

    const val CHAT_DETAIL_ROUTE = "chat/{convId}?initial={initial}" // 会话详情（initial=首页带来的第一条）
    fun chatDetail(convId: String, initial: String = "") =
        "chat/$convId" + if (initial.isNotBlank()) "?initial=" + java.net.URLEncoder.encode(initial, "UTF-8") else ""

    const val LOGIN_ROUTE = "login?next={next}"
    fun login(next: String) = "login?next=$next"

    // 以下为兼容旧文件保留（已被底部 Tab 取代，未在导航中注册，仅防止旧残留文件编译报错）
    const val HOME = "home"
    const val WORKBENCH = "workbench"
    const val SETTINGS = "settings"
    const val CHAT = "chat"
    const val ACTIVITY = "activity"
}
