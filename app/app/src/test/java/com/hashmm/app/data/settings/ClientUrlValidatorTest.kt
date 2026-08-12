package com.hashmm.app.data.settings

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * V306 客户端/云同步地址校验单元测试（纯 JVM，java.net.URI；`./gradlew test` 可跑）。
 * 对齐 APP-P0-04：拒 userinfo/畸形/非 http(s)/缺主机，不强制 HTTPS（局域网 HTTP 需要），归一化去尾斜杠。
 */
class ClientUrlValidatorTest {

    @Test fun accepts_https() {
        assertEquals("https://api.hashmm.com", ClientUrlValidator.normalizeOrNull("https://api.hashmm.com"))
    }

    @Test fun accepts_lan_http() {
        // 局域网 HTTP 必须放行（AutoDL 私有云常是明文 HTTP）
        assertEquals("http://192.168.1.50:6006", ClientUrlValidator.normalizeOrNull("http://192.168.1.50:6006/"))
    }

    @Test fun trims_and_strips_trailing_slash() {
        assertEquals("https://api.example.com/path",
            ClientUrlValidator.normalizeOrNull("  https://api.example.com/path/  "))
    }

    @Test fun rejects_userinfo() {
        assertNull("含账号密码的地址必须拒绝（钓鱼向量）",
            ClientUrlValidator.normalizeOrNull("https://user:pass@evil.com"))
    }

    @Test fun rejects_non_http_scheme() {
        assertNull(ClientUrlValidator.normalizeOrNull("ftp://host"))
        assertNull(ClientUrlValidator.normalizeOrNull("javascript:alert(1)"))
    }

    @Test fun rejects_missing_host() {
        assertNull(ClientUrlValidator.normalizeOrNull("http:///nohost"))
        assertNull(ClientUrlValidator.normalizeOrNull("https://"))
    }

    @Test fun rejects_blank_and_malformed() {
        assertNull(ClientUrlValidator.normalizeOrNull(""))
        assertNull(ClientUrlValidator.normalizeOrNull("   "))
        assertNull(ClientUrlValidator.normalizeOrNull(null))
        assertNull(ClientUrlValidator.normalizeOrNull("not a url"))
    }

    @Test fun isValid_matchesNormalize() {
        assertEquals(true, ClientUrlValidator.isValid("https://ok.com"))
        assertEquals(false, ClientUrlValidator.isValid("https://user:pw@x.com"))
    }
}
