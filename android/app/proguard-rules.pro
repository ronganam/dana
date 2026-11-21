# R8 for the release builds (Compose relies on it for speed on the tablet and the TV).

# ONNX Runtime ships no consumer rules; its JNI code looks these classes up by name.
-keep class ai.onnxruntime.** { *; }

# Readable stack traces and logs (class names show up in log lines).
-dontobfuscate
-keepattributes SourceFile,LineNumberTable

# Compile-time annotations (errorprone, via a play-services dependency) reference javac classes.
-dontwarn javax.lang.model.element.Modifier
