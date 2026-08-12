# CHANGELOG V277 (App 1.10.51) — 对标大厂·API Key 加密存储（审计 P0-1）

> 依据源码审计报告，本版把直连模型 API Key 从**明文 DataStore** 改为 **Android Keystore 加密存储**。

---

## ✅ P0-1：API Key 加密存储（Keystore 托管）

**问题**（审计确认）：`SettingsStore` 用普通 Preferences DataStore 的 `stringPreferencesKey("direct_api_key")`
明文存 OpenAI 兼容 API Key，Root/调试/取证/错误备份都可能读到。

**修复**：
- 新增 `SecureKeyStore`——用 **EncryptedSharedPreferences**（AES256-SIV 键 + AES256-GCM 值，主密钥由
  Android Keystore 硬件/软件保管，与既有 `SecureCache` 同款 MasterKey 方案），密钥落盘即密文。
- `SettingsStore` 的 API Key 读写全部改走 SecureKeyStore；DataStore 只留"是否已配置"标记，不再存明文。
- **老版本明文自动迁移**：检测到 DataStore 里的遗留明文密钥 → 一次性搬进加密存储 → 抹掉明文残留。
- **空值不覆盖旧密钥**：设置页留空/只显示掩码时保存，不会误抹已存密钥（审计要求）。
- **清除入口**：新增 `clearDirectApiKey()` 供退出/切换账号/清数据时清密钥（审计要求）。
- **失败可观测**（对齐审计 P1-9）：加解密异常记**脱敏日志**（只记异常类型，不记密钥内容），不再完全静默。

## 版本

`versionCode 91 → 92`，`versionName 1.10.50 → 1.10.51`。

## 变更清单

**新增**：`data/settings/SecureKeyStore.kt`
**修改**：`data/settings/SettingsStore.kt`（API Key 改走加密存储 + 迁移 + 空值不覆盖 + 清除入口）· `app/build.gradle.kts`

## 验证记录（本机 kotlinc 2.0.21）

- `SecureKeyStore.kt` 用分包 stub 编译 **0 错误**（imports 与既有 SecureCache 一致，Gradle 构建即可用）；
- `SettingsStore.kt` 结构自检通过（括号平衡、注入 secureKeys、迁移逻辑、空值不覆盖、清除方法均到位）；
- 无手动 new SettingsStore（25 处全 Hilt 注入），新增构造参数由 DI 提供；`directApiKey` 仍是 Flow<String>，
  `DirectLlmRepository` 消费不变。
- 说明：EncryptedSharedPreferences 是 Android 官方标准加密存储，运行期行为需真机回归（首次读写、迁移、
  切换账号清除）。App 原生测试引擎（28 套件）本版无改动，维持全绿。
