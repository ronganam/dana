import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// Same release keystore as :app — phone and wear share applicationId and MUST
// be signed with the same key (see AGENTS.md "Wear companion signing").
val keystoreProperties = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "io.github.ronganam.dana.wear"
    compileSdk = 37

    defaultConfig {
        applicationId = "io.github.ronganam.dana"
        minSdk = 30
        targetSdk = 34
        versionCode = 57
        versionName = "8.6.0"
    }

    signingConfigs {
        if (keystoreProperties.isNotEmpty()) {
            create("release") {
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias", "dana")
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
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}


dependencies {
    implementation("androidx.core:core-ktx:1.19.1")
    implementation(project(":protocol"))
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.13.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("com.google.android.gms:play-services-wearable:20.0.1")
    constraints {
        // play-services-base pulls fragment 1.1.0; the permission request (ActivityResult API) needs 1.3+.
        implementation("androidx.fragment:fragment:1.5.4")
    }

    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.1")

    // Wear Compose
    implementation("androidx.wear.compose:compose-material:1.7.2")
    implementation("androidx.wear.compose:compose-foundation:1.7.2")
    implementation("androidx.wear.compose:compose-navigation:1.7.2")

    // Watch Face Complications
    implementation("androidx.wear.watchface:watchface-complications-data-source:1.3.0")
    implementation("androidx.wear.watchface:watchface-complications-data-source-ktx:1.3.0")

    // Wear Tiles
    implementation("androidx.wear.tiles:tiles:1.6.2")
    implementation("androidx.wear.protolayout:protolayout:1.4.2")
    implementation("androidx.wear.protolayout:protolayout-material:1.4.2")
    implementation("com.google.guava:guava:33.8.0-android")

    // JVM tests of the pure shopping model (same version as :app)
    testImplementation("junit:junit:4.13.2")
}
