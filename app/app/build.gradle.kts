import java.io.FileInputStream
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

// ─── 读 Supabase 配置：优先 local.properties（机器本地、可覆盖），否则 gradle.properties（随项目提交）──
// 这样 local.properties 可只交给 Android Studio 管理 sdk.dir，无需手填凭证。
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) FileInputStream(f).use { load(it) }
}
fun localProp(key: String, fallback: String = ""): String =
    localProps.getProperty(key)?.takeIf { it.isNotBlank() }
        ?: (project.findProperty(key) as? String)?.takeIf { it.isNotBlank() }
        ?: fallback

val releaseStoreFile = System.getenv("HASHMM_ANDROID_KEYSTORE")?.takeIf { it.isNotBlank() }
val releaseStorePassword = System.getenv("HASHMM_ANDROID_STORE_PASSWORD")?.takeIf { it.isNotBlank() }
val releaseKeyAlias = System.getenv("HASHMM_ANDROID_KEY_ALIAS")?.takeIf { it.isNotBlank() }
val releaseKeyPassword = System.getenv("HASHMM_ANDROID_KEY_PASSWORD")?.takeIf { it.isNotBlank() }
val releaseSigningReady = listOf(
    releaseStoreFile,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
).all { it != null }

android {
    namespace = "com.hashmm.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.hashmm.app"   // ← 独立包名，和 HashLens(com.hashlens.app) 并存，不覆盖
        minSdk = 26
        targetSdk = 36
        versionCode = 280
        versionName = "12.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Supabase（仅用于登录鉴权；publishable key 可公开，配合后端校验）
        buildConfigField("String", "SUPABASE_URL", "\"${localProp("SUPABASE_URL")}\"")
        buildConfigField("String", "SUPABASE_KEY", "\"${localProp("SUPABASE_PUBLISHABLE_KEY")}\"")
        // 默认的 HashMM 客户端地址（用户可在 App 设置里改）。留空则首次进入引导填写。
        buildConfigField("String", "DEFAULT_CLIENT_URL", "\"${localProp("DEFAULT_CLIENT_URL", "https://hashmm.hashlens.org")}\"")
    }

    signingConfigs {
        if (releaseSigningReady) {
            create("release") {
                storeFile = file(requireNotNull(releaseStoreFile))
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (releaseSigningReady) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
        debug {
            // 调试包加后缀，可与 release 包同机共存
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
            excludes += "/META-INF/INDEX.LIST"
            excludes += "/META-INF/DEPENDENCIES"
        }
    }
}

dependencies {
    implementation(libs.core.ktx)
    implementation(libs.activity.compose)

    // Compose
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    implementation(libs.compose.animation)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.ui.test.junit4)

    // Lifecycle
    implementation(libs.lifecycle.runtime.ktx)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.lifecycle.viewmodel.compose)

    // Navigation
    implementation(libs.navigation.compose)

    // Hilt
    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)
    implementation(libs.hilt.navigation.compose)

    // DataStore
    implementation(libs.datastore.preferences)

    // Coroutines + 序列化
    implementation(libs.coroutines.android)
    implementation(libs.serialization.json)

    // V270 单元测试（app/src/test，纯 JVM）：JUnit4。coroutines-core 经 implementation 已对
    // 单测编译类路径可见（runBlocking/launch/CompletableDeferred 直接可用），无需 coroutines-test。
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)  // V306: 网络层集成测试(真实 HTTP 往返)
    testImplementation(libs.coroutines.android)   // runBlocking 用

    // OkHttp（WebSocket 信令）
    implementation(libs.okhttp.core)
    implementation(libs.okhttp.logging)

    // WebRTC（远程控制投屏）
    implementation(libs.webrtc)

    // Supabase（仅 Auth）
    implementation(platform(libs.supabase.bom))
    implementation(libs.supabase.auth)
    implementation(libs.supabase.postgrest)
    implementation(libs.supabase.realtime)
    implementation(libs.androidx.security.crypto)
    implementation(libs.ktor.client.okhttp)

    // 日志
    implementation(libs.timber)
}
