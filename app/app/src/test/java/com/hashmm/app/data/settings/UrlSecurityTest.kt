package com.hashmm.app.data.settings

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * V308：token 外泄（APP-P0）的回归护栏。
 *
 * 原 ChatLiveRepository.fileViewUrl 对【任何】以 http 开头的绝对地址都追加登录 token，
 * 不校验它是否属于已配置的后端 —— 服务端一旦返回攻击者控制的下载地址，App 就会主动把
 * 用户 token 发过去。这些用例锁死"只给同源地址带凭证"这一契约。
 */
class UrlSecurityTest {

    private val base = "https://api.example.com"

    // ── 同源判定 ──
    @Test fun sameHostSameScheme_isSameOrigin() {
        assertTrue(UrlSecurity.isSameOrigin("https://api.example.com/files/a.pdf", base))
    }

    @Test fun differentHost_isNotSameOrigin() {
        assertFalse(UrlSecurity.isSameOrigin("https://evil.com/steal", base))
    }

    @Test fun subdomain_isNotSameOrigin() {
        // 子域名不算同源（evil.api.example.com 与 api.example.com 是不同主机）
        assertFalse(UrlSecurity.isSameOrigin("https://evil.api.example.com/x", base))
    }

    @Test fun hostPrefixTrick_isNotSameOrigin() {
        // api.example.com.evil.com —— 前缀像但主机完全不同
        assertFalse(UrlSecurity.isSameOrigin("https://api.example.com.evil.com/x", base))
    }

    @Test fun differentScheme_isNotSameOrigin() {
        assertFalse(UrlSecurity.isSameOrigin("http://api.example.com/x", base))
    }

    @Test fun differentPort_isNotSameOrigin() {
        assertFalse(UrlSecurity.isSameOrigin("https://api.example.com:8443/x", base))
    }

    @Test fun defaultPortsAreEquivalent() {
        assertTrue(UrlSecurity.isSameOrigin("https://api.example.com:443/x", base))
        assertTrue(UrlSecurity.isSameOrigin("http://h.com:80/x", "http://h.com"))
    }

    @Test fun malformedOrEmpty_isNotSameOrigin() {
        assertFalse(UrlSecurity.isSameOrigin("", base))
        assertFalse(UrlSecurity.isSameOrigin("not a url", base))
        assertFalse(UrlSecurity.isSameOrigin("https://api.example.com/x", ""))
        assertFalse(UrlSecurity.isSameOrigin(null, base))
    }

    // ── token 追加：只对同源 ──
    @Test fun appendsTokenOnlyForSameOrigin() {
        val same = UrlSecurity.appendTokenIfSameOrigin(
            "https://api.example.com/files/a.pdf", base, "TK",
        )
        assertEquals("https://api.example.com/files/a.pdf?token=TK", same)
    }

    @Test fun neverAppendsTokenToForeignHost() {
        val foreign = "https://evil.com/collect"
        val out = UrlSecurity.appendTokenIfSameOrigin(foreign, base, "TK")
        assertEquals("外部域名绝不能带 token", foreign, out)
        assertFalse("输出中不得出现 token", out.contains("token"))
        assertFalse("输出中不得出现 token 值", out.contains("TK"))
    }

    @Test fun neverAppendsTokenToHttpDowngrade() {
        // HTTPS 后端 + 返回 http:// 地址（降级）→ 不同源 → 不带 token
        val out = UrlSecurity.appendTokenIfSameOrigin("http://api.example.com/f", base, "TK")
        assertFalse("协议降级的地址不得带 token", out.contains("TK"))
    }

    @Test fun existingTokenParamIsNotDuplicated() {
        val u = "https://api.example.com/f?token=OLD"
        assertEquals(u, UrlSecurity.appendTokenIfSameOrigin(u, base, "NEW"))
    }

    @Test fun appendsWithAmpersandWhenQueryExists() {
        val out = UrlSecurity.appendTokenIfSameOrigin(
            "https://api.example.com/f?a=1", base, "TK",
        )
        assertEquals("https://api.example.com/f?a=1&token=TK", out)
    }
}
