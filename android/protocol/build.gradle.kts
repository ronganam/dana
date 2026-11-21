import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// The phone ↔ watch contract: Data Layer paths and the payloads both :app and
// :wear read and write (docs/wear.md, "Phone bridge"). No UI, no resources.
plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "io.github.ronganam.dana.protocol"
    compileSdk = 37

    defaultConfig {
        minSdk = 26
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    // DataMap for the /dana_config and /dana/version data items.
    implementation("com.google.android.gms:play-services-wearable:20.0.1")
    // The JSON payloads (WearJson), the same version as :app and :wear.
    api("org.jetbrains.kotlinx:kotlinx-serialization-json:1.13.0")

    testImplementation("junit:junit:4.13.2")
}
