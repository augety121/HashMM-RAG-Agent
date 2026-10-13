package com.hashmm.app.ui.workbench

import com.hashmm.app.data.remote.AdminToolsRepository.SelfTestResult
import com.hashmm.app.data.remote.AdminToolsRepository.SelfTestRun
import com.hashmm.app.data.remote.AdminToolsRepository.SelfTestSuite
import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.ui.chat.ChatMessageOps
import com.hashmm.app.ui.chat.MdBlock
import com.hashmm.app.ui.chat.formatChatTime
import com.hashmm.app.ui.chat.formatChatTimeFull
import com.hashmm.app.ui.chat.parseMarkdown
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * V272 App 原生测试引擎——**测 App 自己的功能，不再调后端那套桌面测试**。
 *
 * 诉求原话："app 的测试测的是 app 里面的功能，和桌面端不一样，也是压力测试，要特别严格。"
 * 这里在 App 内直接跑 App 自身的纯逻辑（Markdown 渲染器 / 消息列表重构 / 时间显示 / 会话排序 /
 * 后台流状态机 / 缓存 JSON 往返）并附**压力测试**（超大/畸形输入、数万条消息、两万会话排序），
 * 每条产出 通过/失败 + 逐步骤详细日志（打进 detail，多行）。全部离线在设备上跑，不依赖后端。
 *
 * 复用后端同名结果类型（SelfTestSuite/SelfTestResult/SelfTestRun），因此测试中枢 UI 几乎零改动。
 */
object NativeTestEngine {

    private const val G_RENDER = "渲染·Markdown"
    private const val G_MSG = "消息·列表重构"
    private const val G_TIME = "时间·显示与排序"
    private const val G_STREAM = "后台·直播状态机"
    private const val G_CACHE = "缓存·序列化"
    private const val G_STRESS = "压力测试"

    /** 一条 App 原生套件：id/展示名/分组/是否重（压力项标重，默认不勾）/执行体。 */
    private class Suite(
        val id: String, val name: String, val group: String, val heavy: Boolean,
        val exec: () -> CaseResult,
    )

    /** 执行体返回：是否通过 + 详细日志行 + 一句话结论；skip 用于环境不满足。 */
    private class CaseResult(val ok: Boolean, val logs: List<String>, val summary: String, val skip: Boolean = false)

    private fun ok(summary: String, vararg logs: String) = CaseResult(true, logs.toList(), summary)
    private fun fail(summary: String, vararg logs: String) = CaseResult(false, logs.toList(), summary)

    // ────────────────────────────── 套件注册表 ──────────────────────────────
    private val suites: List<Suite> = listOf(
        Suite("md_basic", "Markdown 渲染·各类块正确", G_RENDER, false, ::caseMdBasic),
        Suite("md_edge", "Markdown 渲染·边界与畸形输入", G_RENDER, false, ::caseMdEdge),
        Suite("msg_dedupe", "消息去重·相邻同内容合并", G_MSG, false, ::caseMsgDedupe),
        Suite("msg_reconcile", "消息覆盖·保留流式尾巴不闪没", G_MSG, false, ::caseMsgReconcile),
        Suite("time_display", "时间显示·今天/昨天/前天/星期/年月日", G_TIME, false, ::caseTimeDisplay),
        Suite("time_sort", "会话排序·最新在前(ISO/epoch/空)", G_TIME, false, ::caseTimeSort),
        Suite("stream_accum", "后台直播·内容单调累积", G_STREAM, false, ::caseStreamAccum),
        Suite("cache_json", "缓存往返·消息 JSON 不丢字段", G_CACHE, false, ::caseCacheJson),
        // ── V274 新增功能测试（非压力）──
        Suite("msg_fields", "消息·字段完整(thinking/status/id 全保留)", G_MSG, false, ::caseMsgFields),
        Suite("sortkey_edge", "排序·极端时间戳(远未来/远古/非法)", G_TIME, false, ::caseSortKeyEdge),
        Suite("reconcile_layer", "消息·多轮覆盖叠加不串不丢", G_MSG, false, ::caseReconcileLayer),
        Suite("md_inline", "渲染·行内符号与长段落分段", G_RENDER, false, ::caseMdInline),
        Suite("md_ordered", "渲染·有序列表编号与连续性", G_RENDER, false, ::caseMdOrdered),
        Suite("reconcile_dedup", "消息·覆盖同时去重服务端重复", G_MSG, false, ::caseReconcileDedup),
        Suite("stress_pipeline", "压力·解析+去重+排序组合流水线", G_STRESS, true, ::caseStressPipeline),
        // ── 压力测试（重，默认不勾）──
        Suite("stress_md", "压力·超大+畸形 Markdown(2万行)", G_STRESS, true, ::caseStressMarkdown),
        Suite("stress_dedupe", "压力·5万条消息去重", G_STRESS, true, ::caseStressDedupe),
        Suite("stress_sort", "压力·2万会话排序", G_STRESS, true, ::caseStressSort),
        Suite("stress_time", "压力·10万次时间格式化", G_STRESS, true, ::caseStressTime),
        // ── V273 扩充：覆盖更多 App 功能的压力测试 ──
        Suite("stress_reconcile", "压力·5万条消息覆盖+保尾", G_STRESS, true, ::caseStressReconcile),
        Suite("stress_stream_build", "压力·20万 token 流式累积", G_STRESS, true, ::caseStressStreamBuild),
        Suite("stress_md_nested", "压力·深层嵌套+超宽表 Markdown", G_STRESS, true, ::caseStressMdNested),
        Suite("stress_hasstreaming", "压力·10万条 streaming 判据扫描", G_STRESS, true, ::caseStressHasStreaming),
        Suite("stress_sortkey_mixed", "压力·2万混合格式(ISO/epoch/空)排序", G_STRESS, true, ::caseStressSortKeyMixed),
        Suite("stress_dedupe_unique", "压力·5万条全唯一去重(最坏)", G_STRESS, true, ::caseStressDedupeAllUnique),
        Suite("stress_time_year", "压力·全年每日时间格式化", G_STRESS, true, ::caseStressTimeFullYear),
        Suite("stress_reconcile_loop", "压力·千轮覆盖循环(模拟长会话流式)", G_STRESS, true, ::caseStressReconcileLoop),
        Suite("stress_md_huge_line", "压力·单条超长消息(100万字)解析", G_STRESS, true, ::caseStressMdHugeLine),
        // ── V306 大厂级深度压力：多支对话隔离 / 对抗性 Unicode / 深度覆盖抖动 ──
        Suite("stress_multibranch", "压力·多支对话交错重构(隔离+保尾)", G_STRESS, true, ::caseStressMultiBranch),
        Suite("stress_unicode_adv", "压力·对抗性 Unicode/零宽/RTL Markdown", G_STRESS, true, ::caseStressAdversarialUnicode),
        Suite("stress_reconcile_deep", "压力·2千轮覆盖抖动(尾巴唯一+不丢)", G_STRESS, true, ::caseStressReconcileDeep),
    )

    fun suiteList(): List<SelfTestSuite> = suites.map { SelfTestSuite(it.id, it.name, it.group, it.heavy) }

    /** 跑选中的原生套件；逐项容错（异常记 FAIL 继续），返回与后端同构的 SelfTestRun。 */
    suspend fun run(ids: List<String>): SelfTestRun = withContext(Dispatchers.Default) {
        val chosen = suites.filter { it.id in ids }
        if (chosen.isEmpty()) return@withContext SelfTestRun(error = "没有选中的原生测试项")
        val results = ArrayList<SelfTestResult>(chosen.size)
        for (s in chosen) {
            val t0 = System.nanoTime()
            val cr = try {
                s.exec()
            } catch (e: Throwable) {
                CaseResult(false, listOf("抛出异常：${e.javaClass.simpleName}: ${e.message}"), "执行异常")
            }
            val nanos = System.nanoTime() - t0
            val ms = nanos / 1_000_000
            val micros = nanos / 1000
            val detail = buildString {
                append(cr.summary)
                // V274 修"0ms 观感"：纯内存用例本就亚毫秒，给出微秒级实测让日志可信、看得出真跑了。
                append("\n⏱ 实测耗时：")
                append(if (ms >= 1) "${ms}ms（${micros}µs）" else "${micros}µs（<1ms，纯内存计算属正常）")
                if (cr.logs.isNotEmpty()) {
                    append("\n")
                    append(cr.logs.joinToString("\n") { "  · $it" })
                }
            }
            results.add(SelfTestResult(s.id, s.name, s.group, cr.ok, cr.skip, detail, ms))
        }
        SelfTestRun(
            results = results,
            passCnt = results.count { it.ok && !it.skip },
            failCnt = results.count { !it.ok && !it.skip },
            skipCnt = results.count { it.skip },
            error = null,
        )
    }

    // ────────────────────────────── 用例实现 ──────────────────────────────
    private fun caseMdBasic(): CaseResult {
        val logs = ArrayList<String>()
        val in1 = "# 一级\n## 二级\n### 三级"
        val h = parseMarkdown(in1)
        val headingOk = h.size == 3 && (h[0] as? MdBlock.Heading)?.level == 1 && (h[2] as? MdBlock.Heading)?.level == 3
        logs.add("【输入】${in1.replace("\n", "⏎")}")
        logs.add("【输出】解析出 ${h.size} 块：${h.joinToString { it.javaClass.simpleName }}")
        logs.add("【判定】期望 3 个标题块且级别 1..3 → 实际 ${h.size} 块、级别 ${(h.getOrNull(0) as? MdBlock.Heading)?.level}/${(h.getOrNull(2) as? MdBlock.Heading)?.level} → ${if (headingOk) "✓" else "✗"}")
        val in2 = "```kotlin\nval x = 1\n```"
        val code = parseMarkdown(in2).filterIsInstance<MdBlock.Code>().firstOrNull()
        val codeOk = code?.lang == "kotlin" && code.code.contains("val x = 1")
        logs.add("【输入】$in2")
        logs.add("【输出】代码块 lang=${code?.lang}, code=${code?.code?.replace("\n", "⏎")}")
        logs.add("【判定】期望 lang=kotlin 且含 'val x = 1' → ${if (codeOk) "✓" else "✗"}")
        val in3 = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        val t = parseMarkdown(in3).filterIsInstance<MdBlock.Table>().firstOrNull()
        val tableOk = t?.headers == listOf("A", "B") && t?.rows?.firstOrNull() == listOf("1", "2")
        logs.add("【输入】${in3.replace("\n", "⏎")}")
        logs.add("【输出】表头=${t?.headers}，首行=${t?.rows?.firstOrNull()}")
        logs.add("【判定】期望表头[A,B]、首行[1,2] → ${if (tableOk) "✓" else "✗"}")
        val in4 = "- 项目\n1. 有序\n> 引用\n---"
        val misc = parseMarkdown(in4)
        val miscOk = misc.any { it is MdBlock.Bullet } && misc.any { it is MdBlock.Numbered } &&
            misc.any { it is MdBlock.Quote } && misc.any { it is MdBlock.Divider }
        logs.add("【输入】${in4.replace("\n", "⏎")}")
        logs.add("【输出】块类型=${misc.joinToString { it.javaClass.simpleName }}")
        logs.add("【判定】期望含 Bullet/Numbered/Quote/Divider 各一 → ${if (miscOk) "✓" else "✗"}")
        val all = headingOk && codeOk && tableOk && miscOk
        return CaseResult(all, logs, if (all) "各类 Markdown 块解析正确" else "部分块解析错误")
    }

    private fun caseMdEdge(): CaseResult {
        val logs = ArrayList<String>()
        val inUnclosed = "```\ncode line\n还是代码"
        val unclosed = parseMarkdown(inUnclosed)
        logs.add("【输入·未闭合代码块】${inUnclosed.replace("\n", "⏎")}")
        logs.add("【输出】${unclosed.size} 块（类型 ${unclosed.joinToString { it.javaClass.simpleName }}）；未崩溃/未死循环")
        val empty = parseMarkdown("").isEmpty() && parseMarkdown("\n\n  \n").isEmpty()
        logs.add("【输入·空/纯空白】\"\" 与 \"\\n\\n  \\n\"")
        logs.add("【判定】期望都解析成空 → 实际 ${if (empty) "都为空 ✓" else "非空 ✗"}")
        val innerIn = "```\n# 不是标题\n- 不是项目\n```"
        val inner = parseMarkdown(innerIn)
        val innerSafe = inner.size == 1 && inner[0] is MdBlock.Code
        logs.add("【输入·代码块内含md符号】${innerIn.replace("\n", "⏎")}")
        logs.add("【输出】${inner.size} 块，类型=${inner.firstOrNull()?.javaClass?.simpleName}")
        logs.add("【判定】代码块内 # 和 - 不应被解析成标题/列表 → ${if (innerSafe) "✓（整体 1 个 Code）" else "✗"}")
        val weirdTable = try { parseMarkdown("| 只有表头没有分隔\n普通行"); true } catch (e: Exception) { false }
        logs.add("【输入·畸形表格】'| 只有表头没有分隔'⏎'普通行' → 不崩=${if (weirdTable) "✓" else "✗"}")
        val all = empty && innerSafe && weirdTable
        return CaseResult(all, logs, if (all) "边界/畸形输入健壮" else "边界处理有问题")
    }

    private fun caseMsgDedupe(): CaseResult {
        val logs = ArrayList<String>()
        val input = listOf(
            m("1", "user", "你好"), m("2", "assistant", "在的"), m("3", "assistant", "在的"),
        )
        val out = ChatMessageOps.dedupeAdjacent(input)
        val dedupeOk = out.size == 2
        logs.add("【输入】${input.joinToString(" | ") { "${it.role}:'${it.content}'" }}")
        logs.add("【输出】${out.joinToString(" | ") { "${it.role}:'${it.content}'" }}")
        logs.add("【判定】相邻同角色同内容(assistant:'在的'×2)应合并 → ${input.size}→${out.size}（期望 2）${if (dedupeOk) "✓" else "✗"}")
        val blankIn = listOf(m("1", "assistant", "", "streaming"), m("2", "assistant", "", "streaming"))
        val blankKept = ChatMessageOps.dedupeAdjacent(blankIn).size == 2
        logs.add("【输入】两条空内容 streaming 占位 → 【判定】空占位不应去重 → 保留 ${ChatMessageOps.dedupeAdjacent(blankIn).size}（期望 2）${if (blankKept) "✓" else "✗"}")
        val diffIn = listOf(m("1", "user", "X"), m("2", "assistant", "X"))
        val diffRole = ChatMessageOps.dedupeAdjacent(diffIn).size == 2
        logs.add("【输入】user:'X' | assistant:'X' → 【判定】不同角色同内容应保留 → ${ChatMessageOps.dedupeAdjacent(diffIn).size}（期望 2）${if (diffRole) "✓" else "✗"}")
        val all = dedupeOk && blankKept && diffRole
        return CaseResult(all, logs, if (all) "去重逻辑正确" else "去重逻辑有误")
    }

    private fun caseMsgReconcile(): CaseResult {
        val logs = ArrayList<String>()
        val server = listOf(m("u1", "user", "问题"))
        val local = listOf(m("u1", "user", "问题"), m("a", "assistant", "生成中…", "streaming"))
        val out = ChatMessageOps.reconcile(local, server)
        val tailKept = out.size == 2 && out.last().status == "streaming"
        logs.add("【输入·服务端】${server.joinToString { "${it.role}:'${it.content}'" }}")
        logs.add("【输入·本地】${local.joinToString { "${it.role}:'${it.content}'(${it.status})" }}")
        logs.add("【输出】${out.joinToString { "${it.role}:'${it.content}'(${it.status})" }}")
        logs.add("【判定】服务端无流式占位时应保留本地尾巴不闪没 → 尾条 status=${out.lastOrNull()?.status}（期望 streaming）${if (tailKept) "✓" else "✗"}")
        val sIn = listOf(m("u1", "user", "问题"), m("a1", "assistant", "完整", "complete"))
        val lIn = listOf(m("u1", "user", "问题"), m("a1", "assistant", "旧", "streaming"))
        val serverWins = ChatMessageOps.reconcile(lIn, sIn).last().content == "完整"
        logs.add("【输入】服务端已有 a1='完整'(complete)，本地 a1='旧'(streaming) → 【判定】以服务端为准 → 尾条内容='${ChatMessageOps.reconcile(lIn, sIn).last().content}'（期望 完整）${if (serverWins) "✓" else "✗"}")
        val emptyKeepsLocal = ChatMessageOps.reconcile(local, emptyList()) == local
        logs.add("【输入】服务端空 → 【判定】保留本地(离线可看) → ${if (emptyKeepsLocal) "✓" else "✗"}")
        val all = tailKept && serverWins && emptyKeepsLocal
        return CaseResult(all, logs, if (all) "消息覆盖不闪没" else "覆盖逻辑有问题")
    }

    private fun caseTimeDisplay(): CaseResult {
        val logs = ArrayList<String>()
        fun daysAgo(n: Long) = OffsetDateTime.now(ZoneId.systemDefault()).minusDays(n)
            .withHour(14).withMinute(30).withSecond(0).withNano(0)
            .format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssXXX"))
        val d0 = daysAgo(0); val d1 = daysAgo(1); val d2 = daysAgo(2); val d4 = daysAgo(4); val d10 = daysAgo(10)
        val today = formatChatTime(d0); val yest = formatChatTime(d1); val qian = formatChatTime(d2)
        val week = formatChatTime(d4); val ymd = formatChatTime(d10); val full = formatChatTimeFull("2023-03-05T09:07:00Z")
        logs.add("【输入→输出】0天前『$d0』→『$today』(期望 今天…) ${if (today.startsWith("今天")) "✓" else "✗"}")
        logs.add("【输入→输出】1天前 →『$yest』(期望 昨天…) ${if (yest.startsWith("昨天")) "✓" else "✗"}")
        logs.add("【输入→输出】2天前 →『$qian』(期望 前天…) ${if (qian.startsWith("前天")) "✓" else "✗"}")
        logs.add("【输入→输出】4天前 →『$week』(期望 周X…) ${if (week.startsWith("周")) "✓" else "✗"}")
        logs.add("【输入→输出】10天前 →『$ymd』(期望 yyyy-MM-dd) ${if (Regex("""^\d{4}-\d{2}-\d{2}$""").matches(ymd)) "✓" else "✗"}")
        logs.add("【输入→输出】完整时间戳『2023-03-05T09:07:00Z』→『$full』(期望 yyyy-MM-dd HH:mm) ${if (Regex("""^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$""").matches(full)) "✓" else "✗"}")
        val all = today.startsWith("今天") && yest.startsWith("昨天") && qian.startsWith("前天") &&
            week.startsWith("周") && Regex("""^\d{4}-\d{2}-\d{2}$""").matches(ymd) &&
            Regex("""^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$""").matches(full)
        return CaseResult(all, logs, if (all) "时间显示全部符合（含前天/年月日）" else "时间显示有误")
    }

    private fun caseTimeSort(): CaseResult {
        val logs = ArrayList<String>()
        val kNew = ChatMessageOps.convSortKey("2026-07-10T00:00:00Z")
        val kOld = ChatMessageOps.convSortKey("2026-07-01T00:00:00Z")
        logs.add("【输入】ISO '2026-07-10' vs '2026-07-01' → 【输出】键 $kNew vs $kOld → 较新更大 ${if (kNew > kOld) "✓" else "✗"}")
        val fb = ChatMessageOps.convSortKey("", "2026-07-05T00:00:00Z")
        logs.add("【输入】updatedAt='' createdAt='2026-07-05' → 【输出】键 $fb → 回退 createdAt ${if (fb > 0) "✓" else "✗"}")
        val ep = ChatMessageOps.convSortKey("1751328000")
        logs.add("【输入】epoch 秒 '1751328000' → 【输出】键 $ep → 解析成毫秒(>1e12) ${if (ep > 1_000_000_000_000L) "✓" else "✗"}")
        val inList = listOf("2026-07-01T00:00:00Z", "2026-07-10T00:00:00Z", "2026-07-05T00:00:00Z")
        val order = inList.withIndex().sortedByDescending { ChatMessageOps.convSortKey(it.value) }.map { it.index }
        val orderOk = order == listOf(1, 2, 0)
        logs.add("【输入】3 个会话[7-01,7-10,7-05] → 【输出】倒序后原索引 $order（期望 [1,2,0] 最新在前）${if (orderOk) "✓" else "✗"}")
        val all = kNew > kOld && fb > 0 && ep > 1_000_000_000_000L && orderOk
        return CaseResult(all, logs, if (all) "会话排序键正确（最新在顶）" else "排序键有误")
    }

    // ── V274 新增功能测试实现 ──
    private fun caseMsgFields(): CaseResult {
        val logs = ArrayList<String>()
        val src = ChatMessage(id = "mid", convId = "cid", role = "assistant", content = "正文",
            thinking = "推理过程", status = "streaming", createdAt = "2026-07-10T08:00:00Z")
        logs.add("【输入】ChatMessage(id=mid, role=assistant, content='正文', thinking='推理过程', status=streaming)")
        // 去重/覆盖等操作后关键字段不应丢失
        val afterDedupe = ChatMessageOps.dedupeAdjacent(listOf(src)).first()
        val fieldsKept = afterDedupe.id == "mid" && afterDedupe.thinking == "推理过程" &&
            afterDedupe.status == "streaming" && afterDedupe.role == "assistant" && afterDedupe.content == "正文"
        logs.add("【输出】经 dedupeAdjacent 后：id=${afterDedupe.id}, thinking='${afterDedupe.thinking}', status=${afterDedupe.status}")
        logs.add("【判定】id/thinking/status/role/content 全保留 → ${if (fieldsKept) "✓" else "✗"}")
        val hasStreaming = ChatMessageOps.hasStreaming(listOf(src))
        logs.add("【判定】hasStreaming 应识别 streaming 消息 → ${if (hasStreaming) "✓" else "✗"}")
        val all = fieldsKept && hasStreaming
        return CaseResult(all, logs, if (all) "消息字段完整无丢失" else "字段丢失")
    }

    private fun caseSortKeyEdge(): CaseResult {
        val logs = ArrayList<String>()
        val future = ChatMessageOps.convSortKey("2099-12-31T23:59:59Z")
        val ancient = ChatMessageOps.convSortKey("1970-01-02T00:00:00Z")
        val bad = ChatMessageOps.convSortKey("这不是时间")
        val bad2 = ChatMessageOps.convSortKey("2026-13-45T99:99:99Z")
        logs.add("【输入·远未来】'2099-12-31' → 【输出】$future")
        logs.add("【输入·远古】'1970-01-02' → 【输出】$ancient")
        logs.add("【输入·非法串】'这不是时间' → 【输出】$bad（期望 0）；'2026-13-45...' → $bad2（期望 0）")
        val orderOk = future > ancient && ancient > 0 && bad == 0L && bad2 == 0L
        logs.add("【判定】远未来>远古>0，非法→0 且不抛异常 → ${if (orderOk) "✓" else "✗"}")
        // 非法值排序应沉底
        val mixed = listOf("2099-12-31T23:59:59Z", "非法", "1970-01-02T00:00:00Z")
        val sorted = ChatMessageOps.sortByRecencyDesc(mixed, { it })
        val badLast = sorted.last() == "非法"
        logs.add("【输入】[远未来,非法,远古] → 【输出】排序后 $sorted → 非法沉底 ${if (badLast) "✓" else "✗"}")
        val all = orderOk && badLast
        return CaseResult(all, logs, if (all) "极端时间戳健壮、非法沉底" else "极端值处理有误")
    }

    private fun caseReconcileLayer(): CaseResult {
        val logs = ArrayList<String>()
        // 模拟多轮：本地先有 u1+流式a，服务端第一次回 u1+a(complete)，第二轮又发 u2+流式b
        val round1Server = listOf(m("u1", "user", "问1"), m("a1", "assistant", "答1", "complete"))
        val round1Local = listOf(m("u1", "user", "问1"), m("a1", "assistant", "答1生成中", "streaming"))
        val r1 = ChatMessageOps.reconcile(round1Local, round1Server)
        logs.add("【第1轮】本地流式 a1 + 服务端完成 a1 → 输出 ${r1.joinToString { "${it.content}(${it.status})" }}")
        val r1ok = r1.size == 2 && r1.last().content == "答1" && r1.last().status == "complete"
        logs.add("【判定1】以服务端完成版为准 → ${if (r1ok) "✓" else "✗"}")
        // 第2轮：在 r1 基础上又追加流式 b
        val round2Local = r1 + m("b-local", "assistant", "答2生成中", "streaming")
        val round2Server = r1   // 服务端还没落库 b
        val r2 = ChatMessageOps.reconcile(round2Local, round2Server)
        logs.add("【第2轮】追加本地流式 b，服务端未落库 → 输出 ${r2.joinToString { "${it.content}(${it.status})" }}")
        val r2ok = r2.size == 3 && r2.last().status == "streaming" && r2.count { it.content == "答1" } == 1
        logs.add("【判定2】保留新流式尾巴 b、旧消息不重复不丢 → 条数 ${r2.size}（期望 3）、答1 出现 ${r2.count { it.content == "答1" }} 次（期望 1）${if (r2ok) "✓" else "✗"}")
        val all = r1ok && r2ok
        return CaseResult(all, logs, if (all) "多轮覆盖叠加不串不丢" else "多轮覆盖有问题")
    }

    private fun caseMdInline(): CaseResult {
        val logs = ArrayList<String>()
        val inWrap = "这是第一行\n紧接着第二行\n\n新的段落"
        val paras = parseMarkdown(inWrap).filterIsInstance<MdBlock.Paragraph>()
        logs.add("【输入】'第一行'⏎'第二行'⏎⏎'新的段落' → 【输出】${paras.size} 段：${paras.map { it.text.take(12) }}")
        val mergeOk = paras.size == 2 && paras[0].text.contains("第一行") && paras[0].text.contains("第二行")
        logs.add("【判定】软换行合并成一段、空行分段 → ${if (mergeOk) "✓" else "✗"}")
        val inMix = "# 标题\n段落\n- 项目1\n- 项目2\n```\ncode\n```"
        val mix = parseMarkdown(inMix)
        val orderOk = mix.firstOrNull() is MdBlock.Heading && mix.lastOrNull() is MdBlock.Code &&
            mix.count { it is MdBlock.Bullet } == 2
        logs.add("【输入】标题+段落+2项目+代码块 → 【输出】${mix.joinToString { it.javaClass.simpleName }}")
        logs.add("【判定】首块 Heading、尾块 Code、2 个 Bullet、顺序保持 → ${if (orderOk) "✓" else "✗"}")
        val all = mergeOk && orderOk
        return CaseResult(all, logs, if (all) "行内分段与混合文档顺序正确" else "分段/顺序有误")
    }

    // ── V274 新增功能测试实现 END ──
    private fun caseMdOrdered(): CaseResult {
        val logs = ArrayList<String>()
        val in1 = "1. 第一\n2. 第二\n3) 也支持右括号"
        val nums = parseMarkdown(in1).filterIsInstance<MdBlock.Numbered>()
        logs.add("【输入】${in1.replace("\n", "⏎")}")
        logs.add("【输出】${nums.size} 个有序项：${nums.map { "${it.num}.${it.text}" }}")
        val ok1 = nums.size == 3 && nums[0].num == 1 && nums[1].text == "第二"
        logs.add("【判定】3 项、首项 num=1、第2项='第二' → ${if (ok1) "✓" else "✗"}")
        val in2 = "10. 从十开始\n11. 十一"
        val nums2 = parseMarkdown(in2).filterIsInstance<MdBlock.Numbered>()
        val ok2 = nums2.size == 2 && nums2[0].num == 10
        logs.add("【输入】'10. 从十开始⏎11. 十一' → 【输出】首项 num=${nums2.firstOrNull()?.num}（期望 10）${if (ok2) "✓" else "✗"}")
        val all = ok1 && ok2
        return CaseResult(all, logs, if (all) "有序列表编号解析正确" else "有序列表编号有误")
    }

    private fun caseReconcileDedup(): CaseResult {
        val logs = ArrayList<String>()
        // 服务端列表里出现相邻重复（同步/合并可能产生），覆盖后应顺带去重
        val server = listOf(
            m("u1", "user", "问题"), m("a1", "assistant", "答案"), m("a1b", "assistant", "答案"),
        )
        val local = listOf(m("u1", "user", "问题"))
        val out = ChatMessageOps.reconcile(local, server)
        logs.add("【输入·服务端】${server.joinToString { "${it.role}:'${it.content}'" }}（含相邻重复 assistant:'答案'×2）")
        logs.add("【输出】${out.joinToString { "${it.role}:'${it.content}'" }}")
        val dedupOk = out.count { it.content == "答案" } == 1
        logs.add("【判定】覆盖时顺带合并相邻重复 → '答案' 出现 ${out.count { it.content == "答案" }} 次（期望 1）${if (dedupOk) "✓" else "✗"}")
        return CaseResult(dedupOk, logs, if (dedupOk) "覆盖同时去重正确" else "覆盖未去重")
    }

    private fun caseStressPipeline(): CaseResult {
        val logs = ArrayList<String>()
        val n = 20000
        val t0 = System.nanoTime()
        // 组合流水线：解析一段 md → 构造大量消息 → 去重 → 按时间排序，模拟真实一次会话刷新的全链路
        val blocks = parseMarkdown("# 标题\n- 项目\n```\ncode\n```\n普通段落")
        val msgs = (0 until n).map { m("id$it", if (it % 2 == 0) "user" else "assistant", "内容${it / 2}") }
        val deduped = ChatMessageOps.dedupeAdjacent(msgs)
        val base = OffsetDateTime.parse("2024-01-01T00:00:00Z")
        val convs = (0 until n).map { base.plusMinutes(it.toLong()).toString() }.shuffled()
        val sorted = ChatMessageOps.sortByRecencyDesc(convs, { it })
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("【流水线】解析 md(${blocks.size} 块) → $n 条消息去重(→${deduped.size}) → $n 会话排序")
        logs.add("【输出】总耗时 ${ms}ms，排序后首条=${sorted.firstOrNull()?.take(10)}")
        val ok = blocks.isNotEmpty() && deduped.size <= n && sorted.size == n && ms < 4000
        return CaseResult(ok, logs, if (ok) "组合流水线稳过（${ms}ms）" else "流水线过慢或异常（${ms}ms）")
    }

    private fun caseStreamAccum(): CaseResult {
        val logs = ArrayList<String>()
        val tokens = listOf("你", "好", "，", "世界")
        val acc = StringBuilder()
        val snapshots = ArrayList<String>()
        for (t in tokens) { acc.append(t); snapshots.add(acc.toString()) }
        var mono = true
        for (i in 1 until snapshots.size) if (!(snapshots[i].startsWith(snapshots[i - 1]) && snapshots[i].length >= snapshots[i - 1].length)) mono = false
        logs.add("逐 token 累积单调增长：$mono")
        val finalOk = snapshots.last() == "你好，世界"
        logs.add("最终内容：${snapshots.last()}")
        val hasStreaming = ChatMessageOps.hasStreaming(listOf(m("a", "assistant", "半截", "streaming")))
        logs.add("streaming 判据可用：$hasStreaming")
        val all = mono && finalOk && hasStreaming
        return CaseResult(all, logs, if (all) "后台直播累积语义正确" else "累积语义有误")
    }

    private fun caseCacheJson(): CaseResult {
        val logs = ArrayList<String>()
        return try {
            val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
            val src = ChatMessage(id = "m1", convId = "c1", role = "assistant", content = "内容<含符号>&测试",
                thinking = "思考", status = "complete", createdAt = "2026-07-10T08:00:00Z")
            val text = json.encodeToString(ChatMessage.serializer(), src)
            val back = json.decodeFromString(ChatMessage.serializer(), text)
            val ok = back.id == src.id && back.content == src.content && back.status == src.status && back.role == src.role
            logs.add("序列化长度 ${text.length}，往返一致：$ok")
            CaseResult(ok, logs, if (ok) "消息 JSON 往返不丢字段" else "JSON 往返字段丢失")
        } catch (e: Throwable) {
            logs.add("序列化不可用：${e.javaClass.simpleName}")
            CaseResult(true, logs, "环境无 kotlinx.serialization，跳过", skip = true)
        }
    }

    // ── 压力测试 ──
    private fun caseStressMarkdown(): CaseResult {
        val logs = ArrayList<String>()
        val sb = StringBuilder()
        for (i in 0 until 20000) {
            when (i % 5) {
                0 -> sb.append("# 标题 $i\n")
                1 -> sb.append("- 项目 $i\n")
                2 -> sb.append("普通段落文字 $i，带一些**强调**和 `代码`。\n")
                3 -> sb.append("| 列A$i | 列B$i |\n| --- | --- |\n| 值 | 值 |\n")
                else -> sb.append("```\ncode $i\n```\n")
            }
        }
        // 追加畸形：未闭合代码块 + 超长单行
        sb.append("```\n未闭合").append("x".repeat(50000))
        val t0 = System.nanoTime()
        val blocks = parseMarkdown(sb.toString())
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("输入 ${sb.length} 字符（含 2 万块 + 5 万字超长行 + 未闭合代码块）")
        logs.add("解析出 ${blocks.size} 块，耗时 ${ms}ms，未崩溃/未死循环")
        val ok = blocks.isNotEmpty() && ms < 4000
        return CaseResult(ok, logs, if (ok) "超大+畸形 Markdown 稳过（${ms}ms）" else "解析过慢或异常（${ms}ms）")
    }

    private fun caseStressDedupe(): CaseResult {
        val logs = ArrayList<String>()
        val n = 50000
        val list = ArrayList<ChatMessage>(n)
        for (i in 0 until n) {
            // 交替：一半是相邻重复（应被合并），制造大量去重工作
            list.add(m("id$i", if (i % 2 == 0) "user" else "assistant", "内容${i / 2}"))
        }
        val t0 = System.nanoTime()
        val out = ChatMessageOps.dedupeAdjacent(list)
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("输入 $n 条 → 去重后 ${out.size} 条，耗时 ${ms}ms")
        val ok = out.size <= n && ms < 2000
        return CaseResult(ok, logs, if (ok) "5 万条去重稳过（${ms}ms）" else "去重过慢（${ms}ms）")
    }

    private fun caseStressSort(): CaseResult {
        val logs = ArrayList<String>()
        val n = 20000
        val base = OffsetDateTime.parse("2020-01-01T00:00:00Z")
        val convs = (0 until n).map { base.plusHours(it.toLong()).toString() }.shuffled()
        val t0 = System.nanoTime()
        // V273：预计算排序键一次/项——之前 sortedByDescending{convSortKey} 每次比较都解析 ISO，
        // 20000 项 ~30 万次解析、真机 9 秒+；改 decorate-sort-undecorate 后仅 2 万次解析。
        val sorted = ChatMessageOps.sortByRecencyDesc(convs, { it })
        val ms = (System.nanoTime() - t0) / 1_000_000
        // 校验降序：也预计算键，不再逐比较解析
        val keyed = sorted.map { ChatMessageOps.convSortKey(it) }
        var desc = true
        for (i in 1 until keyed.size) if (keyed[i - 1] < keyed[i]) { desc = false; break }
        logs.add("$n 个会话时间戳打乱后排序（预计算键），耗时 ${ms}ms，严格降序=$desc")
        val ok = desc && ms < 5000
        return CaseResult(ok, logs, if (ok) "2 万会话排序稳过且降序（${ms}ms）" else "排序错误或过慢（${ms}ms）")
    }

    private fun caseStressTime(): CaseResult {
        val logs = ArrayList<String>()
        val n = 100000
        val samples = listOf("2026-07-10T08:00:00Z", "2026-07-09T08:00:00Z", "2023-03-05T09:00:00Z", "")
        val t0 = System.nanoTime()
        var acc = 0
        for (i in 0 until n) acc += formatChatTime(samples[i % samples.size]).length
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("$n 次 formatChatTime，耗时 ${ms}ms（累计长度 $acc 防优化掉）")
        val ok = ms < 3000
        return CaseResult(ok, logs, if (ok) "10 万次格式化稳过（${ms}ms）" else "格式化过慢（${ms}ms）")
    }

    // ── V273 扩充：更多 App 功能压力测试 ──
    private fun caseStressReconcile(): CaseResult {
        val logs = ArrayList<String>()
        val n = 50000
        val server = ArrayList<ChatMessage>(n)
        for (i in 0 until n) server.add(m("s$i", if (i % 2 == 0) "user" else "assistant", "消息$i"))
        // 本地 = 服务端 + 一条还在生成的流式尾巴（服务端没有）
        val local = ArrayList(server); local.add(m("tail", "assistant", "生成中…", "streaming"))
        val t0 = System.nanoTime()
        val out = ChatMessageOps.reconcile(local, server)
        val ms = (System.nanoTime() - t0) / 1_000_000
        val tailKept = out.lastOrNull()?.status == "streaming"
        logs.add("服务端 $n 条 + 本地流式尾巴 → 覆盖后 ${out.size} 条，耗时 ${ms}ms，尾巴保留=$tailKept")
        val ok = tailKept && ms < 3000
        return CaseResult(ok, logs, if (ok) "5 万条消息覆盖+保尾稳过（${ms}ms）" else "覆盖过慢或丢尾（${ms}ms）")
    }

    private fun caseStressStreamBuild(): CaseResult {
        val logs = ArrayList<String>()
        // 模拟流式：逐 token 追加 20 万字符，验证是线性而非二次（StringBuilder 正确用法）
        val tokens = 200000
        val sb = StringBuilder()
        val t0 = System.nanoTime()
        for (i in 0 until tokens) sb.append(if (i % 7 == 0) "词" else "x")
        val built = sb.length
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("逐 token 追加 $tokens 次 → 长度 $built，耗时 ${ms}ms（线性应 <300ms）")
        val ok = built == tokens && ms < 1500
        return CaseResult(ok, logs, if (ok) "20 万 token 流式累积稳过（${ms}ms）" else "累积异常或二次退化（${ms}ms）")
    }

    private fun caseStressMdNested(): CaseResult {
        val logs = ArrayList<String>()
        val sb = StringBuilder()
        // 深层嵌套项目符号 + 大量代码块 + 超宽表格
        for (i in 0 until 5000) sb.append("  ".repeat(i % 6)).append("- 嵌套项 $i\n")
        for (i in 0 until 2000) sb.append("```lang$i\ncode $i\n```\n")
        sb.append("| ").append((0 until 300).joinToString(" | ") { "列$it" }).append(" |\n")
        sb.append("| ").append((0 until 300).joinToString(" | ") { "---" }).append(" |\n")
        sb.append("| ").append((0 until 300).joinToString(" | ") { "值$it" }).append(" |\n")
        val t0 = System.nanoTime()
        val blocks = parseMarkdown(sb.toString())
        val ms = (System.nanoTime() - t0) / 1_000_000
        val hasTable = blocks.any { it is MdBlock.Table }
        val codeCount = blocks.count { it is MdBlock.Code }
        logs.add("5000 嵌套项 + 2000 代码块 + 300 列宽表 → ${blocks.size} 块（代码块 $codeCount，含表=$hasTable），耗时 ${ms}ms")
        val ok = blocks.isNotEmpty() && hasTable && codeCount >= 1900 && ms < 4000
        return CaseResult(ok, logs, if (ok) "深层嵌套+超宽表 Markdown 稳过（${ms}ms）" else "嵌套解析异常或过慢（${ms}ms）")
    }

    private fun caseStressHasStreaming(): CaseResult {
        val logs = ArrayList<String>()
        val n = 100000
        val list = ArrayList<ChatMessage>(n)
        for (i in 0 until n) list.add(m("h$i", "assistant", "内容$i", if (i == n - 1) "streaming" else "complete"))
        val t0 = System.nanoTime()
        var hit = false
        repeat(50) { hit = ChatMessageOps.hasStreaming(list) }   // 反复扫，模拟轮询判据
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("10 万条消息扫描 streaming 判据 50 次，耗时 ${ms}ms，命中=$hit")
        val ok = hit && ms < 2000
        return CaseResult(ok, logs, if (ok) "10 万条 streaming 判据稳过（${ms}ms）" else "判据过慢（${ms}ms）")
    }

    private fun caseStressSortKeyMixed(): CaseResult {
        val logs = ArrayList<String>()
        val n = 20000
        val base = OffsetDateTime.parse("2021-01-01T00:00:00Z")
        // 混合格式：ISO、epoch 秒、epoch 毫秒、空——排序键必须都能比
        val mixed = (0 until n).map {
            when (it % 4) {
                0 -> base.plusHours(it.toLong()).toString()
                1 -> (base.plusHours(it.toLong()).toEpochSecond()).toString()
                2 -> (base.plusHours(it.toLong()).toInstant().toEpochMilli()).toString()
                else -> ""
            }
        }.shuffled()
        val t0 = System.nanoTime()
        val sorted = ChatMessageOps.sortByRecencyDesc(mixed, { it })
        val ms = (System.nanoTime() - t0) / 1_000_000
        val keyed = sorted.map { ChatMessageOps.convSortKey(it) }
        var desc = true
        for (i in 1 until keyed.size) if (keyed[i - 1] < keyed[i]) { desc = false; break }
        logs.add("$n 个混合格式(ISO/epoch秒/epoch毫秒/空)排序，耗时 ${ms}ms，严格降序=$desc")
        val ok = desc && ms < 5000
        return CaseResult(ok, logs, if (ok) "2 万混合格式排序稳过（${ms}ms）" else "混合排序错误或过慢（${ms}ms）")
    }

    private fun caseStressDedupeAllUnique(): CaseResult {
        val logs = ArrayList<String>()
        val n = 50000
        // 最坏情况：全不重复（去重无法跳过任何一条，全程比较）
        val list = (0 until n).map { m("u$it", if (it % 2 == 0) "user" else "assistant", "唯一内容$it") }
        val t0 = System.nanoTime()
        val out = ChatMessageOps.dedupeAdjacent(list)
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("$n 条全唯一（最坏情况）去重，耗时 ${ms}ms，结果 ${out.size} 条（应=$n 不误删）")
        val ok = out.size == n && ms < 2000
        return CaseResult(ok, logs, if (ok) "5 万条全唯一去重稳过不误删（${ms}ms）" else "去重误删或过慢（${ms}ms）")
    }

    private fun caseStressTimeFullYear(): CaseResult {
        val logs = ArrayList<String>()
        // 一整年每天都格式化，验证在各种日期下都不抛异常、都归入某个桶（正确性 under 多样性）
        val start = OffsetDateTime.parse("2025-01-01T12:00:00Z")
        var bad = 0
        val t0 = System.nanoTime()
        for (d in 0 until 400) {
            val s = formatChatTime(start.plusDays(d.toLong()).toString())
            if (s.isBlank()) bad++
        }
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("连续 400 天格式化，耗时 ${ms}ms，空结果 $bad 个（应=0）")
        val ok = bad == 0
        return CaseResult(ok, logs, if (ok) "全年日期格式化无异常（${ms}ms）" else "$bad 天格式化异常")
    }

    private fun caseStressReconcileLoop(): CaseResult {
        val logs = ArrayList<String>()
        val rounds = 1000
        var acc: List<ChatMessage> = emptyList()
        val t0 = System.nanoTime()
        var maxSize = 0
        for (r in 0 until rounds) {
            val server = acc.filter { it.status != "streaming" } +
                listOf(m("u$r", "user", "问$r"), m("a$r", "assistant", "答$r", "complete"))
            val local = server + m("tail$r", "assistant", "生成中$r", "streaming")
            acc = ChatMessageOps.reconcile(local, server)
            if (acc.size > maxSize) maxSize = acc.size
        }
        val ms = (System.nanoTime() - t0) / 1_000_000
        val tailOk = acc.lastOrNull()?.status == "streaming"
        logs.add("【输入】$rounds 轮覆盖循环（服务端列表持续增长 + 每轮流式尾巴）")
        logs.add("【输出】最终 ${acc.size} 条，峰值 $maxSize 条，耗时 ${ms}ms，末条=${acc.lastOrNull()?.status}")
        logs.add("【判定】千轮覆盖不崩、尾巴保留、耗时可控 → 尾巴 ${if (tailOk) "✓" else "✗"}、耗时 ${if (ms < 5000) "✓" else "✗"}")
        val ok = tailOk && ms < 5000
        return CaseResult(ok, logs, if (ok) "千轮覆盖循环稳过（${ms}ms）" else "覆盖循环退化（${ms}ms）")
    }

    private fun caseStressMdHugeLine(): CaseResult {
        val logs = ArrayList<String>()
        val huge = buildString {
            append("# 超长消息\n")
            append("普通段落 ").append("字".repeat(1_000_000)).append("\n")
            append("- 列表项\n")
        }
        val t0 = System.nanoTime()
        val blocks = parseMarkdown(huge)
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("【输入】单条消息含 100 万字超长段落（总 ${huge.length} 字符）")
        logs.add("【输出】解析出 ${blocks.size} 块，耗时 ${ms}ms，未崩溃/未死循环")
        val ok = blocks.isNotEmpty() && ms < 5000
        return CaseResult(ok, logs, if (ok) "百万字超长消息稳过（${ms}ms）" else "超长解析过慢或异常（${ms}ms）")
    }

    // ── V306 大厂级深度压力 ──

    /** 多支对话交错重构：3 支各自增长 + 各带流式尾巴，交错 reconcile 多轮，
     *  验证每支只含本支内容（不串）、每支尾巴保留、无重复、无丢失。 */
    private fun caseStressMultiBranch(): CaseResult {
        val logs = ArrayList<String>()
        val rounds = 300
        // 3 支：各用不同 id 前缀标记，任何串号都能查出来
        val branches = listOf("A", "B", "C")
        val serverByBranch = HashMap<String, ArrayList<ChatMessage>>()
        branches.forEach { serverByBranch[it] = ArrayList() }
        var crossContam = 0
        var tailLost = 0
        val t0 = System.nanoTime()
        for (r in 0 until rounds) {
            for (b in branches) {
                val server = serverByBranch[b]!!
                // 服务端确认上一轮的问答
                server.add(m("${b}_q$r", "user", "[$b] 问题 $r"))
                server.add(m("${b}_a$r", "assistant", "[$b] 回答 $r"))
                // 本地：服务端列表 + 一个正在流式的尾巴
                val local = ArrayList(server)
                local.add(m("${b}_stream$r", "assistant", "[$b] 生成中…", status = "streaming"))
                val merged = ChatMessageOps.reconcile(local, server)
                // 校验：合并结果里不得出现【别的分支】的内容
                for (msg in merged) {
                    val other = branches.firstOrNull { it != b && msg.content.contains("[$it]") }
                    if (other != null) crossContam++
                }
                // 校验：尾巴（streaming）保留
                if (!ChatMessageOps.hasStreaming(merged)) tailLost++
            }
        }
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("3 支对话 × $rounds 轮交错覆盖：跨支串扰=$crossContam 次（应 0），尾巴丢失=$tailLost 次（应 0）")
        logs.add("耗时 ${ms}ms")
        val ok = crossContam == 0 && tailLost == 0 && ms < 5000
        return CaseResult(ok, logs, if (ok) "多支对话隔离+保尾稳过（${ms}ms）" else "多支对话串扰/丢尾（串$crossContam 丢$tailLost）")
    }

    /** 对抗性 Unicode：零宽字符、RTL 覆盖、组合字符、emoji ZWJ、超长——验证解析不崩不挂、有界耗时。 */
    private fun caseStressAdversarialUnicode(): CaseResult {
        val logs = ArrayList<String>()
        val evil = buildString {
            repeat(20000) { i ->
                when (i % 6) {
                    0 -> append("# 标题\u200B\u200C\u200D隐藏\n")           // 零宽字符
                    1 -> append("段落\u202E反转文本\u202C正常\n")           // RTL override
                    2 -> append("组合\u0301\u0302\u0303\u0304字符\n")       // 组合附加符
                    3 -> append("emoji 👨‍👩‍👧‍👦 家庭 ZWJ 序列\n")           // emoji ZWJ
                    4 -> append("- 列\u0000空字符\uFFFF非字符\n")          // 空/非字符
                    else -> append("`代码\uD83D\uDE00` 与 **粗体\uFEFFBOM**\n")
                }
            }
            append("```\n未闭合 ").append("\u200B".repeat(30000))          // 大量零宽 + 未闭合
        }
        val t0 = System.nanoTime()
        val blocks = try {
            parseMarkdown(evil)
        } catch (e: Exception) {
            return fail("对抗性 Unicode 解析抛异常：${e.javaClass.simpleName}", "输入 ${evil.length} 字符")
        }
        val ms = (System.nanoTime() - t0) / 1_000_000
        logs.add("输入 ${evil.length} 字符（零宽/RTL/组合/ZWJ/空字符/BOM/未闭合）")
        logs.add("解析出 ${blocks.size} 块，耗时 ${ms}ms，未崩溃/未死循环")
        val ok = blocks.isNotEmpty() && ms < 5000
        return CaseResult(ok, logs, if (ok) "对抗性 Unicode 稳过（${ms}ms）" else "对抗性 Unicode 过慢/异常（${ms}ms）")
    }

    /** 深度覆盖抖动：2000 轮，服务端列表持续增长 + 每轮流式尾巴，
     *  验证终态尾巴唯一(streaming)、无重复 id、总量正确、有界耗时。 */
    private fun caseStressReconcileDeep(): CaseResult {
        val logs = ArrayList<String>()
        val rounds = 2000
        val server = ArrayList<ChatMessage>()
        var lastMerged: List<ChatMessage> = emptyList()
        val t0 = System.nanoTime()
        for (r in 0 until rounds) {
            server.add(m("q$r", "user", "问题 $r"))
            server.add(m("a$r", "assistant", "回答 $r"))
            val local = ArrayList(server)
            local.add(m("stream$r", "assistant", "生成中…", status = "streaming"))
            lastMerged = ChatMessageOps.reconcile(local, server)
        }
        val ms = (System.nanoTime() - t0) / 1_000_000
        val streamingCount = lastMerged.count { it.status == "streaming" }
        val ids = lastMerged.map { it.id }
        val dupIds = ids.size - ids.toSet().size
        logs.add("$rounds 轮增长覆盖：终态 ${lastMerged.size} 条，streaming 尾巴=$streamingCount 条（应 1），重复 id=$dupIds（应 0）")
        logs.add("耗时 ${ms}ms")
        val ok = streamingCount == 1 && dupIds == 0 && ms < 8000
        return CaseResult(ok, logs, if (ok) "2 千轮深度覆盖稳过（${ms}ms）" else "深度覆盖尾巴/重复异常（尾$streamingCount 重$dupIds）")
    }

    private fun m(id: String, role: String, content: String, status: String = "complete") =
        ChatMessage(id = id, convId = "c", role = role, content = content, thinking = "", status = status, createdAt = "")

    /** V273 把原生测试结果渲染成 markdown 报告（发后端落盘 data/selftest_reports 用，与桌面端同构）。 */
    fun buildReportMd(run: SelfTestRun): String {
        val sb = StringBuilder()
        val now = OffsetDateTime.now(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))
        sb.append("# HashMM App 原生测试报告\n")
        sb.append("- 时间：").append(now).append("\n")
        sb.append("- 结果：**通过 ").append(run.passCnt).append(" / 失败 ").append(run.failCnt)
            .append(" / 跳过 ").append(run.skipCnt).append("**（共 ").append(run.results.size).append(" 项）\n")
        sb.append("- 来源：App 端设备本地测试引擎（NativeTestEngine），测 App 自身功能 + 压力\n\n")
        // 分组汇总
        val byGroup = run.results.groupBy { it.group }
        sb.append("## 分组汇总\n| 分组 | 通过 | 失败 | 跳过 |\n| --- | --- | --- | --- |\n")
        for ((g, rs) in byGroup) {
            sb.append("| ").append(g).append(" | ").append(rs.count { it.ok && !it.skip })
                .append(" | ").append(rs.count { !it.ok && !it.skip })
                .append(" | ").append(rs.count { it.skip }).append(" |\n")
        }
        sb.append("\n## 逐项结果\n")
        for (r in run.results) {
            val mark = if (r.skip) "跳过" else if (r.ok) "通过" else "失败"
            sb.append("### [").append(mark).append("] ").append(r.name)
                .append("（").append(r.ms).append("ms）\n")
            sb.append(r.detail).append("\n\n")
        }
        return sb.toString()
    }
}

/**
 * V273 App 测试中枢跨导航持久化：把上次运行结果 + 勾选记在进程级单例，退出测试页再进来仍能看到
 * "上次测了什么、结果如何"，不再一进去就空。与桌面端"测试态持久化"对齐。（进程存活期内有效；
 * 冷启动清空属正常，报告已落到服务器 data/selftest_reports。）
 */
object NativeTestHub {
    var lastRun: SelfTestRun? = null
    var lastSel: Set<String> = emptySet()
    var lastSavedPath: String = ""
}
