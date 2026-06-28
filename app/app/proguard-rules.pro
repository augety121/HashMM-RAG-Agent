# WebRTC（org.webrtc 反射调用，保留）
-keep class org.webrtc.** { *; }
# Supabase / Ktor 序列化
-keepattributes *Annotation*, InnerClasses
-dontwarn kotlinx.serialization.**
-keep,includedescriptorclasses class com.hashmm.app.**$$serializer { *; }
-keepclassmembers class com.hashmm.app.** {
    *** Companion;
}
