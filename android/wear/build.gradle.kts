import java.util.Properties
import org.jetbrains.kotlin.compose.compiler.gradle.ComposeFeatureFlag
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Same release keystore as :app — phone and wear share applicationId and MUST
// be signed with the same key (see AGENTS.md "Wear companion signing").
val keystoreProperties = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "app.ganam.assistant.wear"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.ganam.assistant"
        minSdk = 30
        targetSdk = 34
        versionCode = 56
        versionName = "8.5.0"
    }

    signingConfigs {
        if (keystoreProperties.isNotEmpty()) {
            create("release") {
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // R8 + the baseline profile (src/main/baseline-prof.txt): see
            // AGENTS.md "Watch assistant performance".
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
                ?: signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    buildFeatures {
        compose = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_1_8)
    }
}

composeCompiler {
    // Strong skipping (on by default since Kotlin 2.0.20) changes when
    // composables skip recomposition; kept off so the Kotlin 2 upgrade doesn't
    // change how screens update. The flag goes away in Kotlin 2.5: turn strong
    // skipping on (and test the screens) before moving past 2.4.
    @Suppress("DEPRECATION_ERROR")
    featureFlags.add(ComposeFeatureFlag.StrongSkipping.disabled())
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("com.google.android.gms:play-services-wearable:18.1.0")

    val composeBom = platform("androidx.compose:compose-bom:2024.04.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")

    // Wear Compose
    implementation("androidx.wear.compose:compose-material:1.3.1")
    implementation("androidx.wear.compose:compose-foundation:1.3.1")
    implementation("androidx.wear.compose:compose-navigation:1.3.1")

    // Watch Face Complications
    implementation("androidx.wear.watchface:watchface-complications-data-source:1.2.1")
    implementation("androidx.wear.watchface:watchface-complications-data-source-ktx:1.2.1")

    // Wear Tiles
    implementation("androidx.wear.tiles:tiles:1.4.1")
    implementation("androidx.wear.protolayout:protolayout:1.3.0")
    implementation("androidx.wear.protolayout:protolayout-material:1.3.0")
    implementation("com.google.guava:guava:32.1.3-android")

    // JVM tests of the pure shopping model (same versions as :app; org.json because Android's is a stub off-device)
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
