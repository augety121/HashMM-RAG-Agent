package com.hashmm.app.ui.workbench

import com.hashmm.app.data.remote.AdminToolsRepository
import com.hashmm.app.data.remote.AdvancedData
import com.hashmm.app.data.remote.AuditItem
import com.hashmm.app.data.remote.QualityData

/**
 * V275 工作台数据页跨导航持久化——与测试中枢 NativeTestHub 同款进程级单例。
 *
 * 诉求："其他数据页也这种，不能每次打开都是新的（尤其数据页）。" 之前质量/审计/高级/上下文透视
 * 这些页把拉到的数据放在 Composable 的 remember 里，一离开页面就丢，再进来又空转重拉。这里把
 * 各页"上次拉到的数据"记在单例：重进页面**先秒显上次结果**，不空转；需要最新数据点刷新即可。
 * （进程存活期内有效；冷启动清空属正常。）
 */
object WorkbenchCache {
    var quality: QualityData? = null
    var audit: List<AuditItem>? = null
    var advanced: AdvancedData? = null
    var context: AdminToolsRepository.CtxInspect? = null
}
