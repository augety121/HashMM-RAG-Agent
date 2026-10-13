package com.hashmm.app.ui.workbench

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * V306 WebView 信任边界单元测试（纯 JVM，java.net.URI；`./gradlew test` 可跑）。
 * 对齐 APP-P0-03：origin 归一 + 同源判定，用于导航白名单与"仅受信 origin 才注入令牌"。
 */
class WebViewSecurityTest {

    @Test fun originOf_extractsSchemeHostPort() {
        assertEquals("https://api.hashmm.com", WebViewSecurity.originOf("https://api.hashmm.com/path?q=1"))
        assertEquals("http://127.0.0.1:6006", WebViewSecurity.originOf("http://127.0.0.1:6006/ui"))
    }

    @Test fun originOf_rejectsNonHttp() {
        assertNull(WebViewSecurity.originOf("file:///etc/passwd"))
        assertNull(WebViewSecurity.originOf("javascript:alert(1)"))
        assertNull(WebViewSecurity.originOf(""))
        assertNull(WebViewSecurity.originOf(null))
    }

    @Test fun sameOrigin_trueForSameSchemeHostPort() {
        assertTrue(WebViewSecurity.isSameOrigin("https://api.hashmm.com/a", "https://api.hashmm.com/b?x=1"))
    }

    @Test fun sameOrigin_falseForSubdomainSpoof() {
        assertFalse("子域伪装不得判同源",
            WebViewSecurity.isSameOrigin("https://api.hashmm.com.evil.com/", "https://api.hashmm.com"))
    }

    @Test fun sameOrigin_falseForPortMismatch() {
        assertFalse(WebViewSecurity.isSameOrigin("http://127.0.0.1:9999/", "http://127.0.0.1:6006/"))
    }

    @Test fun sameOrigin_falseForSchemeMismatch() {
        assertFalse(WebViewSecurity.isSameOrigin("http://api.hashmm.com/", "https://api.hashmm.com/"))
    }

    @Test fun sameOrigin_falseWhenEitherUnparseable() {
        assertFalse(WebViewSecurity.isSameOrigin("not a url", "https://api.hashmm.com"))
        assertFalse(WebViewSecurity.isSameOrigin("https://api.hashmm.com", "file:///x"))
    }

    @Test fun workbenchUrl_containsOnlyNonSecretViewState() {
        val url = buildUrl("https://api.hashmm.com", view = "runs")
        assertEquals("https://api.hashmm.com?view=runs", url)
        assertFalse(url.contains("token"))
        assertFalse(url.contains("refresh"))
    }
}
