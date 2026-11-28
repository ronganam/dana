// Versions of the build toolchain. AGP 9 compiles Kotlin itself (built-in
// Kotlin); the Kotlin Gradle plugin declared here sets the compiler version,
// and the Compose and serialization compiler plugins must carry the same version.
plugins {
    id("com.android.application") version "9.5.3" apply false
    id("com.android.library") version "9.5.1" apply false
    id("org.jetbrains.kotlin.android") version "2.4.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.21" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.4.21" apply false
}
