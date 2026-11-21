# R8 for the watch release build (Compose relies on it for speed).

# Readable stack traces and logs (class names show up in log lines).
-dontobfuscate
-keepattributes SourceFile,LineNumberTable

# Compile-time annotations (errorprone, via a play-services dependency) reference javac classes.
-dontwarn javax.lang.model.element.Modifier
