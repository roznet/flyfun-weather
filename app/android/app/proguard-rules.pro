# Keep kotlinx.serialization generated serializers
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}

-keep,includedescriptorclasses class aero.flyfun.**$$serializer { *; }
-keepclassmembers class aero.flyfun.** {
    *** Companion;
}
-keepclasseswithmembers class aero.flyfun.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# OkHttp & SQLite JDBC
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn org.sqlite.**
