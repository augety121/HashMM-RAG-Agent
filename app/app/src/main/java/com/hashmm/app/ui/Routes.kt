package com.hashmm.app.ui

/** 导航路由常量。主框架是底部 Tab；详情/登录/远程为压栈页面。 */
object Routes {
    const val MAIN = "main"                       // 底部 Tab 主框架
    const val REMOTE = "remote"                   // 远程控制（从「我的」进入）
    const val TERMS = "terms"                     // 用户协议
    const val PRIVACY = "privacy"                 // 隐私政策
    const val MEMORY = "memory"                   // 记忆中心（云端同步）
    const val SCHEDULED = "scheduled_native"      // V218: 定时任务（原生）
    const val RUNS = "runs_native"                // V218: 运行轨迹（原生）
    const val WORK_CANVAS_ROUTE = "work/{runId}"  // 一项工作的原生目标/过程/依据/成果画布
    fun workCanvas(runId: String) =
        "work/" + java.net.URLEncoder.encode(runId, "UTF-8")
    const val AUDIT = "audit_native"              // V219: 权限审计（原生）
    const val QUALITY = "quality_native"          // V219: 质量看板（原生）
    const val SELFTEST = "selftest_native"        // V267: 测试中枢（原生，对标桌面端）
    const val CONTEXT = "context_native"          // V268: 上下文透视（原生，对标桌面端）
    const val ADVANCED = "advanced_native"        // V219: 高级能力（原生概览）
    const val KNOWLEDGE = "knowledge"             // 知识库（直连客户端后端）
    const val MODELS = "models"                   // 模型/后端配置
    const val KG = "kg"                           // 知识图谱
    const val ADMIN = "admin"                     // 管理后台
    const val RELAY = "relay"                     // 接力（设备交接）
    const val USAGE = "usage"                     // 用量
    const val VALIDITY = "validity"               // 失效区（文档时效/归档）
    const val AGENTS_STUDIO = "agents_studio"     // V259: 智能体工坊（原生）
    const val AGENTS_STUDIO_ROUTE = "agents_studio?convId={convId}&goal={goal}"
    fun agentsStudio(convId: String = "", goal: String = "") =
        "agents_studio?convId=" + java.net.URLEncoder.encode(convId, "UTF-8") +
            "&goal=" + java.net.URLEncoder.encode(goal, "UTF-8")
    const val DOC_STUDIO = "doc_studio"           // V259: 文档工坊（原生）
    const val CONTROL_HUB = "control_hub"         // V259: 总控中枢（原生）
    const val CLIENT_CONN = "client_conn"         // 客户端连接（我的 → 独立页）
    const val PERSONAL_INFO = "personal_info"     // 个人信息（头像/昵称/账号）
    const val ABOUT = "about"                     // 关于 HashMM
    const val HELP = "help"                       // 帮助与反馈
    const val DATA_LIST = "data_list"             // 个人信息采集清单

    const val TASK_LAUNCH_ROUTE = "task-launch/{kind}?preset={preset}"
    fun taskLaunch(kind: String, preset: String = "") =
        "task-launch/$kind?preset=" + java.net.URLEncoder.encode(preset, "UTF-8")

    const val CHAT_DETAIL_ROUTE = "chat/{convId}?initial={initial}&dispatch={dispatch}" // 会话详情（initial=首条；dispatch=电脑任务类型，非空则下发到电脑客户端）
    fun chatDetail(convId: String, initial: String = "", dispatch: String = "") =
        "chat/$convId" +
            (if (initial.isNotBlank()) "?initial=" + java.net.URLEncoder.encode(initial, "UTF-8") else "?initial=") +
            (if (dispatch.isNotBlank()) "&dispatch=$dispatch" else "")

    const val LOGIN_ROUTE = "login?next={next}"
    fun login(next: String) = "login?next=$next"

    // 以下为兼容旧文件保留（已被底部 Tab 取代，未在导航中注册，仅防止旧残留文件编译报错）
    const val HOME = "home"
    const val WORKBENCH = "workbench"
    const val SETTINGS = "settings"
    const val CHAT = "chat"
    const val ACTIVITY = "activity"
}
