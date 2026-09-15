# CHANGELOG V270 (App 1.10.44) — 修复构建失败：42 个 Unresolved reference

> 你贴的构建截图：`:app:compileDebugUnitTestKotlin` 42 个错误（`Unresolved reference
> 'junit'/'Test'/'assertEquals'/'runTest'…`）。根因是 **V269 我加了单元测试却没给工程配任何测试
> 依赖**（这个工程此前没有 test 目录，catalog 里连 junit 都没有）——是我的疏漏，本版根治并建立
> "测试文件必须连依赖一起验证"的纪律。

---

## 1️⃣ 根因与修复

**根因两条**：
1. `app/src/test` 下的测试引用 `org.junit.*`，但 `gradle/libs.versions.toml` 没有 junit、
   `build.gradle.kts` 没有任何 `testImplementation` —— 测试编译类路径上没有 JUnit；
2. `LiveChatManagerTest` 还用了 `kotlinx.coroutines.test.runTest`（需要额外的 coroutines-test
   依赖），属于没必要的第二个新依赖。

**修复三处**：
- `gradle/libs.versions.toml`：`[versions]` 加 `junit = "4.13.2"`，`[libraries]` 加
  `junit = { module = "junit:junit", version.ref = "junit" }`（沿用你的版本目录惯例）；
- `app/build.gradle.kts`：依赖块加 `testImplementation(libs.junit)`。**只新增这一个测试依赖**——
  coroutines-core 经 `implementation` 已对单测编译类路径可见，无需 coroutines-test；
- 重写 `LiveChatManagerTest.kt`：`runTest` → **`runBlocking`**（coroutines-core 自带），删掉
  `ExperimentalCoroutinesApi`/coroutines-test import；4 个用例语义不变（内容单调累积、
  后台 Deferred 不随 await 者取消、空流语义）。`ChatFormatTest.kt` 无需改（只缺 JUnit）。

## 2️⃣ 验证（这次把"依赖在位"也验了）

- 用与真实 API 签名对齐的 org.junit + kotlinx.coroutines stub，kotlinc 2.0.21 编译
  `ChatFormat.kt + ChatFormatTest.kt + LiveChatManagerTest.kt`：**错误 0，全部 .class 生成**
  ——42 个 Unresolved 逐一消失；
- 教训入流程：新增测试文件必须同时核对 (a) 依赖声明在 catalog+gradle 双处、(b) 只用主代码
  已有依赖能覆盖的 API（能用 runBlocking 就不引 coroutines-test）。

## 3️⃣ 版本

`versionCode 84 → 85`，`versionName 1.10.43 → 1.10.44`。

## 变更清单

**修改**：`gradle/libs.versions.toml`（junit 版本+库）· `app/build.gradle.kts`
（testImplementation + 版本号）· `app/src/test/.../LiveChatManagerTest.kt`（runBlocking 重写）
