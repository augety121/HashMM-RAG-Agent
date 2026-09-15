package com.hashmm.app.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.clickable
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.style.BaselineShift

/**
 * 轻量 Markdown 渲染（无三方库）。支持：
 * 标题 # ## ###、加粗 **、斜体 *、行内代码 `、无序/有序列表、表格 | |、
 * 代码块 ```、引用 >、分隔线 ---、引用标记 [N]。面向聊天气泡，窄屏优先。
 */
@Composable
fun MarkdownText(
    markdown: String,
    color: Color,
    modifier: Modifier = Modifier,
    fontSize: Int = 15,
) {
    val blocks = remember(markdown) { parseMarkdown(markdown) }
    val codeBg = MaterialTheme.colorScheme.surfaceVariant
    val faint = color.copy(alpha = 0.55f)
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        blocks.forEach { b ->
            when (b) {
                is MdBlock.Heading -> Text(
                    inline(b.text, color, codeBg, faint),
                    fontSize = (fontSize + when (b.level) { 1 -> 7; 2 -> 4; else -> 2 }).sp,
                    fontWeight = FontWeight.Bold, color = color,
                    modifier = Modifier.padding(top = 2.dp),
                )
                is MdBlock.Paragraph -> Text(inline(b.text, color, codeBg, faint), fontSize = fontSize.sp, color = color, lineHeight = (fontSize + 7).sp)
                is MdBlock.Bullet -> Row(Modifier.padding(start = (b.indent * 14).dp)) {
                    Text("•  ", fontSize = fontSize.sp, color = faint)
                    Text(inline(b.text, color, codeBg, faint), fontSize = fontSize.sp, color = color, lineHeight = (fontSize + 7).sp)
                }
                is MdBlock.Numbered -> Row(Modifier.padding(start = (b.indent * 14).dp)) {
                    Text("${b.num}.  ", fontSize = fontSize.sp, color = faint, fontWeight = FontWeight.Medium)
                    Text(inline(b.text, color, codeBg, faint), fontSize = fontSize.sp, color = color, lineHeight = (fontSize + 7).sp)
                }
                is MdBlock.Quote -> Row {
                    Box(Modifier.width(3.dp).heightIn(min = 18.dp).clip(RoundedCornerShape(2.dp)).background(faint))
                    Spacer(Modifier.width(8.dp))
                    Text(inline(b.text, faint, codeBg, faint), fontSize = fontSize.sp, color = faint, fontStyle = FontStyle.Italic)
                }
                is MdBlock.Code -> CodeBlockView(b.code, b.lang, codeBg, color, fontSize)
                is MdBlock.Divider -> Box(Modifier.fillMaxWidth().height(1.dp).background(color.copy(alpha = 0.15f)))
                is MdBlock.Table -> MdTableView(b, color, codeBg, faint, fontSize)
            }
        }
    }
}

@Composable
private fun MdTableView(t: MdBlock.Table, color: Color, codeBg: Color, faint: Color, fontSize: Int) {
    val border = color.copy(alpha = 0.18f)
    val cols = t.headers.size.coerceAtLeast(1)
    Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
        Column(Modifier.clip(RoundedCornerShape(10.dp)).border(1.dp, border, RoundedCornerShape(10.dp))) {
            // 表头
            Row(Modifier.background(codeBg)) {
                t.headers.forEachIndexed { i, h ->
                    TableCell(h, color, codeBg, faint, fontSize, bold = true, last = i == cols - 1, border = border)
                }
            }
            t.rows.forEach { row ->
                Box(Modifier.fillMaxWidth().height(1.dp).background(border))
                Row {
                    for (i in 0 until cols) {
                        TableCell(row.getOrElse(i) { "" }, color, codeBg, faint, fontSize, bold = false, last = i == cols - 1, border = border)
                    }
                }
            }
        }
    }
}

@Composable
private fun TableCell(text: String, color: Color, codeBg: Color, faint: Color, fontSize: Int, bold: Boolean, last: Boolean, border: Color) {
    Box(Modifier.width(132.dp).padding(horizontal = 10.dp, vertical = 8.dp)) {
        Text(
            inline(text.trim(), color, codeBg, faint),
            fontSize = (fontSize - 1).sp, color = color,
            fontWeight = if (bold) FontWeight.SemiBold else FontWeight.Normal,
            lineHeight = (fontSize + 4).sp,
        )
    }
    if (!last) Box(Modifier.width(1.dp).height(40.dp).background(border))
}

private val CitationColor = Color(0xFFEF3E36)   // 引用角标用品牌红
private val LinkColor = Color(0xFF3B82F6)        // 可点击链接用蓝色（下载/外链）

@Composable
private fun CodeBlockView(code: String, lang: String, codeBg: Color, color: Color, fontSize: Int) {
    val clip = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) { if (copied) { kotlinx.coroutines.delay(1500); copied = false } }
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(codeBg)) {
        Row(
            Modifier.fillMaxWidth().padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                if (lang.isBlank()) "代码" else lang.uppercase(),
                fontSize = 10.sp, fontFamily = FontFamily.Monospace,
                color = color.copy(alpha = 0.5f), modifier = Modifier.weight(1f),
            )
            Row(
                Modifier.clip(RoundedCornerShape(6.dp))
                    .clickable { clip.setText(AnnotatedString(code)); copied = true }
                    .padding(horizontal = 7.dp, vertical = 3.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    if (copied) Icons.Outlined.Check else Icons.Outlined.ContentCopy,
                    contentDescription = "复制代码", tint = color.copy(alpha = 0.6f),
                    modifier = Modifier.size(13.dp),
                )
                Spacer(Modifier.width(3.dp))
                Text(if (copied) "已复制" else "复制", fontSize = 10.sp, color = color.copy(alpha = 0.6f))
            }
        }
        Box(Modifier.horizontalScroll(rememberScrollState()).padding(start = 12.dp, end = 12.dp, top = 2.dp, bottom = 12.dp)) {
            Text(code, fontSize = (fontSize - 2).sp, fontFamily = FontFamily.Monospace, color = color, lineHeight = (fontSize + 3).sp)
        }
    }
}

// ---------- 解析 ----------
sealed interface MdBlock {
    data class Heading(val level: Int, val text: String) : MdBlock
    data class Paragraph(val text: String) : MdBlock
    data class Bullet(val text: String, val indent: Int) : MdBlock
    data class Numbered(val num: Int, val text: String, val indent: Int) : MdBlock
    data class Quote(val text: String) : MdBlock
    data class Code(val code: String, val lang: String) : MdBlock
    data class Table(val headers: List<String>, val rows: List<List<String>>) : MdBlock
    data object Divider : MdBlock
}

private val tableRowRegex = Regex("""^\s*\|.*\|\s*$""")
private val tableSepRegex = Regex("""^\s*\|?[\s:\-|]+\|?\s*$""")
private val bulletRegex = Regex("""^(\s*)[-*•]\s+(.*)$""")
private val numberedRegex = Regex("""^(\s*)(\d+)[.)]\s+(.*)$""")

fun parseMarkdown(src: String): List<MdBlock> {
    val lines = src.replace("\r\n", "\n").split("\n")
    val out = ArrayList<MdBlock>()
    var i = 0
    val para = StringBuilder()
    fun flushPara() {
        if (para.isNotBlank()) out.add(MdBlock.Paragraph(para.toString().trim()))
        para.setLength(0)
    }
    while (i < lines.size) {
        val line = lines[i]
        val trimmed = line.trim()
        when {
            trimmed.startsWith("```") -> {
                flushPara()
                val lang = trimmed.removePrefix("```").trim()
                val sb = StringBuilder(); i++
                while (i < lines.size && !lines[i].trim().startsWith("```")) { sb.appendLine(lines[i]); i++ }
                out.add(MdBlock.Code(sb.toString().trimEnd('\n'), lang)); i++
            }
            trimmed.isEmpty() -> { flushPara(); i++ }
            trimmed.matches(Regex("""^#{1,6}\s+.*""")) -> {
                flushPara()
                val level = trimmed.takeWhile { it == '#' }.length
                out.add(MdBlock.Heading(level, trimmed.dropWhile { it == '#' }.trim())); i++
            }
            trimmed == "---" || trimmed == "***" || trimmed == "___" -> { flushPara(); out.add(MdBlock.Divider); i++ }
            // 表格：当前行是 | … | 且下一行是分隔行
            tableRowRegex.matches(line) && i + 1 < lines.size && tableSepRegex.matches(lines[i + 1]) && lines[i + 1].contains("-") -> {
                flushPara()
                val headers = splitRow(line)
                i += 2
                val rows = ArrayList<List<String>>()
                while (i < lines.size && tableRowRegex.matches(lines[i])) { rows.add(splitRow(lines[i])); i++ }
                out.add(MdBlock.Table(headers, rows))
            }
            bulletRegex.matches(line) -> {
                flushPara()
                val m = bulletRegex.find(line)!!
                out.add(MdBlock.Bullet(m.groupValues[2], (m.groupValues[1].length / 2).coerceAtMost(3))); i++
            }
            numberedRegex.matches(line) -> {
                flushPara()
                val m = numberedRegex.find(line)!!
                out.add(MdBlock.Numbered(m.groupValues[2].toIntOrNull() ?: 1, m.groupValues[3], (m.groupValues[1].length / 2).coerceAtMost(3))); i++
            }
            trimmed.startsWith(">") -> { flushPara(); out.add(MdBlock.Quote(trimmed.removePrefix(">").trim())); i++ }
            else -> { if (para.isNotEmpty()) para.append(' '); para.append(trimmed); i++ }
        }
    }
    flushPara()
    return out
}

private fun splitRow(line: String): List<String> {
    var s = line.trim()
    if (s.startsWith("|")) s = s.substring(1)
    if (s.endsWith("|")) s = s.substring(0, s.length - 1)
    return s.split("|").map { it.trim() }
}

// ---------- 行内 ----------
private val citationRegex = Regex("""\[(\d+)]""")

private fun inline(text: String, color: Color, codeBg: Color, faint: Color): AnnotatedString = buildAnnotatedString {
    var i = 0
    while (i < text.length) {
        when {
            text.startsWith("**", i) -> {
                val end = text.indexOf("**", i + 2)
                if (end > i) { pushStyle(SpanStyle(fontWeight = FontWeight.Bold)); append(text.substring(i + 2, end)); pop(); i = end + 2 }
                else { append("**"); i += 2 }
            }
            text[i] == '`' -> {
                val end = text.indexOf('`', i + 1)
                if (end > i) { pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeBg)); append(" " + text.substring(i + 1, end) + " "); pop(); i = end + 1 }
                else { append('`'); i++ }
            }
            text[i] == '*' && i + 1 < text.length && text[i + 1] != ' ' -> {
                val end = text.indexOf('*', i + 1)
                if (end > i) { pushStyle(SpanStyle(fontStyle = FontStyle.Italic)); append(text.substring(i + 1, end)); pop(); i = end + 1 }
                else { append('*'); i++ }
            }
            text[i] == '[' -> {
                // 链接 [文字](url) → 可点击蓝色链接（点开=浏览器下载/打开）。桌面投送的"点此下载"就走这里。
                val close = text.indexOf("](", i + 1)
                val end = if (close in (i + 1) until text.length) text.indexOf(')', close + 2) else -1
                if (close > i && end > close) {
                    val label = text.substring(i + 1, close)
                    val url = text.substring(close + 2, end).trim()
                    withLink(LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = LinkColor, textDecoration = TextDecoration.Underline)))) { append(label) }
                    i = end + 1
                } else {
                    // 不是链接 → 退回到引用角标 [N] 处理
                    val cm = citationRegex.matchAt(text, i)
                    if (cm != null) {
                        pushStyle(SpanStyle(color = CitationColor, fontWeight = FontWeight.Bold, fontSize = 10.sp, baselineShift = BaselineShift.Superscript))
                        append(cm.groupValues[1]); pop(); i = cm.range.last + 1
                    } else { append('['); i++ }
                }
            }
            else -> { append(text[i]); i++ }
        }
    }
}
