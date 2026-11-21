import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// The phone ↔ watch contract: Data Layer paths and the payloads both :app and
// :wear read and write (docs/wear.md, "Phone bridge"). No UI, no resources.
plugins {
    id("com.android.library")
}

android {
    namespace = "io.github.ronganam.dana.protocol"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_1_8)
    }
}

dependencies {
    // DataMap for the /dana_config and /dana/version data items.
    implementation("com.google.android.gms:play-services-wearable:18.1.0")

    testImplementation("junit:junit:4.13.2")
    // Android's org.json is a stub off-device.
    testImplementation("org.json:json:20240303")
}
