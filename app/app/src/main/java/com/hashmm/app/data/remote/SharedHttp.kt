package com.hashmm.app.data.remote

import okhttp3.ConnectionPool
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * SharedHttp — 全 App 共享的 OkHttp 基座（V306，修 APP-P1-02）。
 *
 * 背景：20+ 个 Repository 各自 `OkHttpClient.Builder().build()`，等于各开一套连接池 + 分发器，
 * DNS/TLS 无法复用、超时/重试口径不一致、难统一取消，资源浪费。
 *
 * 方案：只建**一个**基座 `base`（统一连接池、连接超时、失败重试）；各仓库把原来的
 * `OkHttpClient.Builder()` 换成 `SharedHttp.base.newBuilder()`——newBuilder() 会**继承基座的
 * 连接池与分发器**，于是所有派生 client 共享同一连接池，同时各自仍可 `.callTimeout(...)` 定制。
 *
 * 不改任何构造函数 / Hilt 图：仅把"从零建 client"改成"从共享基座派生"，改动面小、风险低。
 */
object SharedHttp {
    val base: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        // 统一连接池：跨仓库复用 keep-alive 连接，省掉重复 DNS/TLS 握手。
        .connectionPool(ConnectionPool(8, 5, TimeUnit.MINUTES))
        // V309 纵深防御：Authorization 头只允许发往登记的后端主机，漏网请求一律剥凭证。
        // 与 UrlSecurity 的同源判断互为双保险（见 CredentialHostGuard.kt）。
        .addInterceptor(CredentialHostGuardInterceptor())
        .build()
}
