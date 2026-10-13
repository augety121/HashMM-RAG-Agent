# ══════════════════════════════════════════════════════════════════════
#  HashMM App 混淆与加固规则（V206）—— 提高逆向门槛，保护业务逻辑。
#  策略：默认全部重命名 + 优化 + 移除日志/行号；只对反射/序列化必须保留的
#  最小集合开 -keep。这样反编译出来的是无意义的 a.a.a()，业务流几乎不可读。
# ══════════════════════════════════════════════════════════════════════

# ── 优化强度：多轮优化 + 允许激进重命名 ──
-optimizationpasses 5
-allowaccessmodification
-mergeinterfacesaggressively
-overloadaggressively
-repackageclasses ''            # 所有类塌进一个空包名，类路径信息全毁

# ── 抹掉调试线索：源文件名、行号、局部变量表 ──
-renamesourcefileattribute ''
-keepattributes !LocalVariableTable,!LocalVariableTypeTable

# ── 移除所有日志调用（连同参数计算），避免日志泄露业务字符串/流程 ──
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
    public static int i(...);
    public static int w(...);
    public static int e(...);
}
-assumenosideeffects class java.io.PrintStream {
    public void println(...);
    public void print(...);
}

# ── 反射/序列化必须保留的最小集合 ──
# WebRTC（org.webrtc 走 JNI + 反射，必须保留原名）
-keep class org.webrtc.** { *; }
-dontwarn org.webrtc.**

# kotlinx.serialization（@Serializable 生成的 $$serializer 靠反射）
-keepattributes RuntimeVisibleAnnotations,AnnotationDefault
-keep,includedescriptorclasses class com.hashmm.app.**$$serializer { *; }
-keepclassmembers class com.hashmm.app.** {
    *** Companion;
    <fields>;
}
-keepclasseswithmembers class com.hashmm.app.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-dontwarn kotlinx.serialization.**

# Ktor / Supabase 客户端引擎（反射加载）
-keep class io.ktor.** { *; }
-dontwarn io.ktor.**
-keep class io.github.jan.supabase.** { *; }
-dontwarn io.github.jan.supabase.**

# Compose 运行时（编译器已处理，保守 dontwarn）
-dontwarn androidx.compose.**

# Hilt 生成代码
-keep class dagger.hilt.** { *; }
-keep class * extends dagger.hilt.android.internal.managers.** { *; }
-dontwarn dagger.hilt.**

# Tink only references these Error Prone types as compile-time annotations.
# Keep the suppression exact so a missing runtime crypto dependency still fails the release build.
-dontwarn com.google.errorprone.annotations.CanIgnoreReturnValue
-dontwarn com.google.errorprone.annotations.CheckReturnValue
-dontwarn com.google.errorprone.annotations.Immutable
-dontwarn com.google.errorprone.annotations.RestrictedApi

# 枚举 values()/valueOf() 反射
-keepclassmembers enum * {
    public static **[] values();
    public static ** valueOf(java.lang.String);
}

# Parcelable
-keepclassmembers class * implements android.os.Parcelable {
    public static final ** CREATOR;
}
