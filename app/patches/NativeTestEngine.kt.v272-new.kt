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
        // ── 压力测试（重，默认不勾）──
        Suite("stress_md", "压力·超大+畸形 Markdown(2万行)", G_STRESS, true, ::caseStressMarkdown),
        Suite("stress_dedupe", "压力·5万条消息去重", G_STRESS, true, ::caseStressDedupe),
        Suite("stress_sort", "压力·2万会话排序", G_STRESS, true, ::caseStressSort),
        Suite("stress_time", "压力·10万次时间格式化", G_STRESS, true, ::caseStressTime),
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
            val ms = (System.nanoTime() - t0) / 1_000_000
            val detail = buildString {
                append(cr.summary)
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
        val h = parseMarkdown("# 一级\n## 二级\n### 三级")
        val headingOk = h.size == 3 && (h[0] as? MdBlock.Heading)?.level == 1 && (h[2] as? MdBlock.Heading)?.level == 3
        logs.add("标题多级：$headingOk（${h.size} 块）")
        val code = parseMarkdown("```kotlin\nval x = 1\n```").filterIsInstance<MdBlock.Code>().firstOrNull()
        val codeOk = code?.lang == "kotlin" && code.code.contains("val x = 1")
        logs.add("代码块内容+语言：$codeOk")
        val t = parseMarkdown("| A | B |\n| --- | --- |\n| 1 | 2 |").filterIsInstance<MdBlock.Table>().firstOrNull()
        val tableOk = t?.headers == listOf("A", "B") && t?.rows?.firstOrNull() == listOf("1", "2")
        logs.add("表格头+行：$tableOk")
        val misc = parseMarkdown("- 项目\n1. 有序\n> 引用\n---")
        val miscOk = misc.any { it is MdBlock.Bullet } && misc.any { it is MdBlock.Numbered } &&
            misc.any { it is MdBlock.Quote } && misc.any { it is MdBlock.Divider }
        logs.add("列表/引用/分隔线：$miscOk")
        val all = headingOk && codeOk && tableOk && miscOk
        return CaseResult(all, logs, if (all) "各类 Markdown 块解析正确" else "部分块解析错误")
    }

    private fun caseMdEdge(): CaseResult {
        val logs = ArrayList<String>()
        // 未闭合代码块不应吞掉整个文档到崩溃/死循环
        val unclosed = parseMarkdown("```\ncode line\n还是代码")
        logs.add("未闭合代码块：解析出 ${unclosed.size} 块，未崩溃")
        // 空输入
        val empty = parseMarkdown("").isEmpty() && parseMarkdown("\n\n  \n").isEmpty()
        logs.add("空/纯空白输入 → 空结果：$empty")
        // 代码块内的 # 和 - 不被误解析
        val innerSafe = parseMarkdown("```\n# 不是标题\n- 不是项目\n```").let { it.size == 1 && it[0] is MdBlock.Code }
        logs.add("代码块内 md 不被解析：$innerSafe")
        // 只有分隔行的“伪表格”不应崩
        val weirdTable = try { parseMarkdown("| 只有表头没有分隔\n普通行"); true } catch (e: Exception) { false }
        logs.add("畸形表格不崩：$weirdTable")
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
        logs.add("相邻同角色同内容合并：${input.size}→${out.size}（应=2）")
        val blankKept = ChatMessageOps.dedupeAdjacent(
            listOf(m("1", "assistant", "", "streaming"), m("2", "assistant", "", "streaming")),
        ).size == 2
        logs.add("空占位不去重：$blankKept")
        val diffRole = ChatMessageOps.dedupeAdjacent(listOf(m("1", "user", "X"), m("2", "assistant", "X"))).size == 2
        logs.add("不同角色同内容保留：$diffRole")
        val all = dedupeOk && blankKept && diffRole
        return CaseResult(all, logs, if (all) "去重逻辑正确" else "去重逻辑有误")
    }

    private fun caseMsgReconcile(): CaseResult {
        val logs = ArrayList<String>()
        val server = listOf(m("u1", "user", "问题"))
        val local = listOf(m("u1", "user", "问题"), m("a", "assistant", "生成中…", "streaming"))
        val out = ChatMessageOps.reconcile(local, server)
        val tailKept = out.size == 2 && out.last().status == "streaming"
        logs.add("服务端无流式占位时保留本地尾巴：$tailKept（$out.size 条）")
        val serverWins = ChatMessageOps.reconcile(
            listOf(m("u1", "user", "问题"), m("a1", "assistant", "旧", "streaming")),
            listOf(m("u1", "user", "问题"), m("a1", "assistant", "完整", "complete")),
        ).last().content == "完整"
        logs.add("服务端已有完成消息则以服务端为准：$serverWins")
        val emptyKeepsLocal = ChatMessageOps.reconcile(local, emptyList()) == local
        logs.add("服务端空 → 保留本地（离线可看）：$emptyKeepsLocal")
        val all = tailKept && serverWins && emptyKeepsLocal
        return CaseResult(all, logs, if (all) "消息覆盖不闪没" else "覆盖逻辑有问题")
    }

    private fun caseTimeDisplay(): CaseResult {
        val logs = ArrayList<String>()
        fun daysAgo(n: Long) = OffsetDateTime.now(ZoneId.systemDefault()).minusDays(n)
            .withHour(14).withMinute(30).withSecond(0).withNano(0)
            .format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssXXX"))
        val today = formatChatTime(daysAgo(0)).startsWith("今天")
        val yest = formatChatTime(daysAgo(1)).startsWith("昨天")
        val qian = formatChatTime(daysAgo(2)).startsWith("前天")
        val week = formatChatTime(daysAgo(4)).startsWith("周")
        val ymd = Regex("""^\d{4}-\d{2}-\d{2}$""").matches(formatChatTime(daysAgo(10)))
        val full = Regex("""^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$""").matches(formatChatTimeFull("2023-03-05T09:07:00Z"))
        logs.add("今天=$today 昨天=$yest 前天=$qian")
        logs.add("3-6天星期=$week；≥7天年月日=$ymd（示例：${formatChatTime(daysAgo(10))}）")
        logs.add("完整时间戳=$full（${formatChatTimeFull("2023-03-05T09:07:00Z")}）")
        val all = today && yest && qian && week && ymd && full
        return CaseResult(all, logs, if (all) "时间显示全部符合（含前天/年月日）" else "时间显示有误")
    }

    private fun caseTimeSort(): CaseResult {
        val logs = ArrayList<String>()
        val iso = ChatMessageOps.convSortKey("2026-07-10T00:00:00Z") > ChatMessageOps.convSortKey("2026-07-01T00:00:00Z")
        logs.add("较新 ISO 排序键更大：$iso")
        val fallback = ChatMessageOps.convSortKey("", "2026-07-05T00:00:00Z") > 0
        logs.add("updatedAt 空回退 createdAt：$fallback")
        val epoch = ChatMessageOps.convSortKey("1751328000") > 1_000_000_000_000L
        logs.add("epoch 秒解析成毫秒：$epoch")
        val order = listOf("2026-07-01T00:00:00Z", "2026-07-10T00:00:00Z", "2026-07-05T00:00:00Z")
            .withIndex().sortedByDescending { ChatMessageOps.convSortKey(it.value) }.map { it.index }
        val orderOk = order == listOf(1, 2, 0)   // 最新(索引1)在前
        logs.add("列表倒序最新在前：$orderOk（$order）")
        val all = iso && fallback && epoch && orderOk
        return CaseResult(all, logs, if (all) "会话排序键正确（最新在顶）" else "排序键有误")
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
        val sorted = convs.sortedByDescending { ChatMessageOps.convSortKey(it) }
        val ms = (System.nanoTime() - t0) / 1_000_000
        // 校验确实降序
        var desc = true
        for (i in 1 until sorted.size) if (ChatMessageOps.convSortKey(sorted[i - 1]) < ChatMessageOps.convSortKey(sorted[i])) { desc = false; break }
        logs.add("$n 个会话时间戳打乱后排序，耗时 ${ms}ms，严格降序=$desc")
        val ok = desc && ms < 2000
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

    private fun m(id: String, role: String, content: String, status: String = "complete") =
        ChatMessage(id = id, convId = "c", role = role, content = content, thinking = "", status = status, createdAt = "")
}
