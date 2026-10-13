package com.hashmm.app.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * CredentialHostGuard 单测（V309）——凭证只发后端主机的纵深防御逻辑。
 *
 * 覆盖：host 提取（含公网 IP:port、带路径、裸 host）、凭证去向策略（同主机放行、异主机剥离、
 * 后端未知保守放行、解析失败剥离）。纯 JVM，无 Android 依赖。
 */
class CredentialHostGuardTest {

    // ── host 提取 ──

    @Test
    fun extractHost_publicIpWithPort() {
        // 用户真实的 AutoDL 公网直连地址
        assertEquals("111.115.7.14", BackendHostHolder.extractHost("http://111.115.7.14:20014"))
        assertEquals("111.115.7.14", BackendHostHolder.extractHost("http://111.115.7.14:20014/api/health"))
    }

    @Test
    fun extractHost_httpsDomain() {
        assertEquals("api.example.com", BackendHostHolder.extractHost("https://api.example.com/x"))
    }

    @Test
    fun extractHost_bareHostGetsHttpsDefault() {
        assertEquals("host.local", BackendHostHolder.extractHost("host.local"))
    }

    @Test
    fun extractHost_caseInsensitive() {
        assertEquals("host.example.com", BackendHostHolder.extractHost("HTTP://Host.Example.COM/x"))
    }

    @Test
    fun extractHost_blankOrGarbage() {
        assertNull(BackendHostHolder.extractHost(""))
        assertNull(BackendHostHolder.extractHost("   "))
        assertNull(BackendHostHolder.extractHost(null))
    }

    // ── 凭证去向策略 ──

    @Test
    fun policy_sameHostAllowsCredential() {
        // 后端 = 111.115.7.14；发往后端自身（含不同端口/路径）→ 允许带 token
        assertTrue(CredentialPolicy.mayCarryCredential("http://111.115.7.14:20014/api/health", "111.115.7.14"))
        assertTrue(CredentialPolicy.mayCarryCredential("http://111.115.7.14:20014/api/conversations", "111.115.7.14"))
    }

    @Test
    fun policy_differentHostStripsCredential() {
        // 后端 = 111.115.7.14；服务端返回一个外部下载地址 → 绝不带 token
        assertFalse(CredentialPolicy.mayCarryCredential("http://evil.com/steal", "111.115.7.14"))
        assertFalse(CredentialPolicy.mayCarryCredential("https://cdn.attacker.net/x", "111.115.7.14"))
    }

    @Test
    fun policy_prefixSpoofDoesNotMatch() {
        // 前缀欺骗：111.115.7.14.evil.com 不是 111.115.7.14
        assertFalse(CredentialPolicy.mayCarryCredential("http://111.115.7.14.evil.com/x", "111.115.7.14"))
    }

    @Test
    fun policy_backendUnknownIsConservativelyAllowed() {
        // 后端主机还没登记（启动早期）→ 保守放行（这些请求极少且非敏感）
        assertTrue(CredentialPolicy.mayCarryCredential("https://api.example.com/x", null))
        assertTrue(CredentialPolicy.mayCarryCredential("https://api.example.com/x", ""))
    }

    @Test
    fun policy_unparseableTargetStripsCredential() {
        // 目标地址解析不出 host → 剥凭证（fail-closed）
        assertFalse(CredentialPolicy.mayCarryCredential("not a url", "111.115.7.14"))
        assertFalse(CredentialPolicy.mayCarryCredential(null, "111.115.7.14"))
    }

    // ── holder 往返 ──

    @Test
    fun holder_setAndGet() {
        BackendHostHolder.setBackendUrl("http://111.115.7.14:20014")
        assertEquals("111.115.7.14", BackendHostHolder.currentHost())
        BackendHostHolder.setBackendUrl("https://newhost.example.com/api")
        assertEquals("newhost.example.com", BackendHostHolder.currentHost())
        BackendHostHolder.setBackendUrl("garbage")
        // "garbage" 会被当作 https://garbage 解析 → host=garbage（合法单标签主机）
        assertEquals("garbage", BackendHostHolder.currentHost())
        BackendHostHolder.setBackendUrl("")
        assertNull(BackendHostHolder.currentHost())
    }
}
