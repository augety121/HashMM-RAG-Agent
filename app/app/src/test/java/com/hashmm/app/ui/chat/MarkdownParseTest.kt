package com.hashmm.app.ui.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * V270 App 专属·Markdown 解析单元测试（测 App 聊天渲染器 parseMarkdown 的正确性，与后端无关）。
 * 覆盖 App 聊天气泡会渲染的各类块：标题/项目符号/有序列表/代码块/表格/引用/分隔线/段落。纯 JVM。
 */
class MarkdownParseTest {

    @Test fun heading_levels() {
        val b = parseMarkdown("# 一级\n## 二级\n### 三级")
        assertEquals(3, b.size)
        assertTrue(b[0] is MdBlock.Heading)
        assertEquals(1, (b[0] as MdBlock.Heading).level)
        assertEquals("二级", (b[1] as MdBlock.Heading).text)
        assertEquals(3, (b[2] as MdBlock.Heading).level)
    }

    @Test fun bullets_and_indent() {
        val b = parseMarkdown("- 顶层\n  - 次层\n* 星号也算")
        val bullets = b.filterIsInstance<MdBlock.Bullet>()
        assertEquals(3, bullets.size)
        assertEquals("顶层", bullets[0].text)
        assertTrue("次层应有更大缩进", bullets[1].indent >= 1)
    }

    @Test fun numbered_list() {
        val b = parseMarkdown("1. 第一\n2. 第二\n3) 也支持右括号")
        val nums = b.filterIsInstance<MdBlock.Numbered>()
        assertEquals(3, nums.size)
        assertEquals(1, nums[0].num)
        assertEquals("第二", nums[1].text)
    }

    @Test fun code_fence_preservesContentAndLang() {
        val src = "```kotlin\nval x = 1\nprintln(x)\n```"
        val b = parseMarkdown(src)
        val code = b.filterIsInstance<MdBlock.Code>().first()
        assertEquals("kotlin", code.lang)
        assertTrue(code.code.contains("val x = 1"))
        assertTrue(code.code.contains("println(x)"))
    }

    @Test fun code_fence_doesNotParseInnerMarkdown() {
        // 代码块内的 # 和 - 不应被当成标题/列表
        val src = "```\n# 这不是标题\n- 这不是项目符号\n```"
        val b = parseMarkdown(src)
        assertEquals(1, b.size)
        assertTrue(b[0] is MdBlock.Code)
    }

    @Test fun table_headersAndRows() {
        val src = "| 名称 | 值 |\n| --- | --- |\n| A | 1 |\n| B | 2 |"
        val b = parseMarkdown(src)
        val t = b.filterIsInstance<MdBlock.Table>().first()
        assertEquals(listOf("名称", "值"), t.headers)
        assertEquals(2, t.rows.size)
        assertEquals(listOf("A", "1"), t.rows[0])
        assertEquals(listOf("B", "2"), t.rows[1])
    }

    @Test fun quote_and_divider() {
        val b = parseMarkdown("> 引用一句\n\n---")
        assertTrue(b.any { it is MdBlock.Quote && it.text == "引用一句" })
        assertTrue(b.any { it is MdBlock.Divider })
    }

    @Test fun paragraph_mergesSoftWrappedLines() {
        val b = parseMarkdown("这是第一行\n紧接着第二行\n\n新的段落")
        val paras = b.filterIsInstance<MdBlock.Paragraph>()
        assertEquals(2, paras.size)
        assertTrue("软换行应合并成一段", paras[0].text.contains("第一行") && paras[0].text.contains("第二行"))
        assertEquals("新的段落", paras[1].text)
    }

    @Test fun mixed_document_orderPreserved() {
        val src = "# 标题\n段落文字\n- 项目1\n- 项目2\n```\ncode\n```"
        val b = parseMarkdown(src)
        assertTrue(b[0] is MdBlock.Heading)
        assertTrue(b.any { it is MdBlock.Paragraph })
        assertEquals(2, b.filterIsInstance<MdBlock.Bullet>().size)
        assertTrue(b.last() is MdBlock.Code)
    }

    @Test fun empty_input_yieldsNothing() {
        assertEquals(0, parseMarkdown("").size)
        assertEquals(0, parseMarkdown("\n\n   \n").size)
    }
}
