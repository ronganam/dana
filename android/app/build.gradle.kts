import java.util.Properties
import org.jetbrains.kotlin.compose.compiler.gradle.ComposeFeatureFlag
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Release signing credentials — see AGENTS.md "Release signing". Loaded from
// keystore.properties at the repo root (gitignored); falls back to debug
// signing when absent so plain `assembleDebug`/`assembleRelease` still works.
val keystoreProperties = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

// Your deployment's site folder (docs/release.md, "Your site"): ../site at the
// repository root, or $DANA_SITE.
val siteDir: File = System.getenv("DANA_SITE")?.takeIf { it.isNotBlank() }?.let { File(it) }
    ?: rootProject.file("../site")

// Firebase (direct push, phone only) is initialised by hand from these values
// instead of the google-services plugin, which would also demand a config for
// the kiosk and TV builds. google-services.json comes from your own Firebase
// project: your site's, else src/phone/ (gitignored; docs/release.md); without it
// the phone build has no direct push.
@Suppress("UNCHECKED_CAST")
val firebaseConfig: Map<String, String> = File(siteDir, "google-services.json")
    .takeIf { it.exists() }.let { it ?: file("src/phone/google-services.json") }.let { gs ->
    if (!gs.exists()) return@let listOf("FIREBASE_PROJECT_ID", "FIREBASE_SENDER_ID", "FIREBASE_APP_ID", "FIREBASE_API_KEY")
        .associateWith { "" }
    val json = groovy.json.JsonSlurper().parse(gs) as Map<String, Any>
    val project = json["project_info"] as Map<String, Any>
    val client = (json["client"] as List<Map<String, Any>>).first {
        ((it["client_info"] as Map<String, Any>)["android_client_info"] as Map<String, Any>)["package_name"] == "io.github.ronganam.dana"
    }
    mapOf(
        "FIREBASE_PROJECT_ID" to project["project_id"].toString(),
        "FIREBASE_SENDER_ID" to project["project_number"].toString(),
        "FIREBASE_APP_ID" to (client["client_info"] as Map<String, Any>)["mobilesdk_app_id"].toString(),
        "FIREBASE_API_KEY" to ((client["api_key"] as List<Map<String, Any>>).first()["current_key"]).toString(),
    )
}

// Your deployment's defaults, from its site folder: android.env (SERVER_URL: the app's
// default server; LAN_DNS_SERVERS: the TV's LAN DNS fallback; KEY=value lines, the
// same file bin/deploy-*.sh source) and tv-defaults.json (the TV's first-run home
// screen, in TvConfig's JSON shape). Without a site the apps have no defaults.
val siteEnv = Properties().apply {
    val f = File(siteDir, "android.env")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun siteValue(key: String): String = siteEnv.getProperty(key, "").trim().removeSurrounding("\"").removeSurrounding("'")
fun quoted(s: String): String =
    "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "") + "\""
val tvDefaultsJson: String = File(siteDir, "tv-defaults.json").let { if (it.exists()) it.readText().trim() else "" }

android {
    namespace = "io.github.ronganam.dana"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.ronganam.dana"
        minSdk = 26
        targetSdk = 36
        versionCode = 56
        versionName = "8.5.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // The server address a fresh install of the kiosk / TV starts with ("": set it in the app).
        buildConfigField("String", "DEFAULT_SERVER_URL", quoted(siteValue("SERVER_URL")))
        // LAN resolvers the TV asks when the system DNS has no answer (comma-separated).
        buildConfigField("String", "LAN_DNS_SERVERS", quoted(siteValue("LAN_DNS_SERVERS")))
        // Release builds talk HTTPS only; ALLOW_CLEARTEXT=true in your site's android.env lets
        // your own build reach a plain-http server on your LAN (debug builds always may:
        // src/debug's network security config). docs/release.md, "HTTPS".
        manifestPlaceholders["usesCleartextTraffic"] = (siteValue("ALLOW_CLEARTEXT") == "true").toString()
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

    // phone: the full app (package io.github.ronganam.dana, pairs with the watch).
    // kiosk: the wall-tablet build — dashboard WebView + "Hey Dana" wake word +
    // the assistant overlay; installs side by side as io.github.ronganam.dana.kiosk.
    // tv: the living-room TV home screen ("Dana TV") — favourite apps, Home
    // Assistant tiles, camera picture-in-picture on remote buttons, "Hey Dana".
    flavorDimensions += "target"
    productFlavors {
        create("phone") {
            dimension = "target"
            isDefault = true
            // R8 runs as a pass-through for the phone until it has been tried there.
            proguardFile("proguard-phone.pro")
            buildConfigField("boolean", "KIOSK", "false")
            buildConfigField("boolean", "TV", "false")
            firebaseConfig.forEach { (k, v) -> buildConfigField("String", k, "\"$v\"") }
        }
        create("kiosk") {
            dimension = "target"
            applicationIdSuffix = ".kiosk"
            buildConfigField("boolean", "KIOSK", "true")
            buildConfigField("boolean", "TV", "false")
            ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        }
        create("tv") {
            dimension = "target"
            applicationIdSuffix = ".tv"
            buildConfigField("boolean", "KIOSK", "false")
            buildConfigField("boolean", "TV", "true")
            buildConfigField("String", "TV_DEFAULTS_JSON", quoted(tvDefaultsJson))
            // The TCL runs a 32-bit userspace (armeabi-v7a only); x86_64 is the emulator.
            ndk { abiFilters += listOf("armeabi-v7a", "x86_64") }
        }
    }

    // Wake word + hands-free assistant session, shared by the kiosk and the TV.
    sourceSets {
        // No direct push on the kiosk and the TV (a no-op PushSetup).
        getByName("kiosk") {
            kotlin.directories += "src/nopush/java"
            kotlin.directories += "src/wake/java"
            assets.directories += "src/wake/assets"
        }
        getByName("tv") {
            kotlin.directories += "src/nopush/java"
            kotlin.directories += "src/wake/java"
            assets.directories += "src/wake/assets"
        }
    }

    buildTypes {
        release {
            // Compose is much slower without R8; the tablet and the TV need it.
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
        buildConfig = true
    }
    // CI runs lintPhoneDebug; the baseline holds the issues lint found when it was
    // added (regenerate only on purpose: ./gradlew :app:updateLintBaseline), so new
    // errors fail the build.
    lint {
        baseline = file("lint-baseline.xml")
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
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("androidx.activity:activity-ktx:1.9.0")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("io.coil-kt:coil-compose:2.5.0")

    val composeBom = platform("androidx.compose:compose-bom:2024.04.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.navigation:navigation-compose:2.7.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")
    implementation("com.google.android.gms:play-services-wearable:18.1.0")
    // The phone ↔ watch Data Layer contract, shared with :wear.
    implementation(project(":protocol"))
    // Retries for queued reminder actions and call-recording uploads.
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    // Phone only: direct push from the server (FCM wake-up pings).
    "phoneImplementation"("com.google.firebase:firebase-messaging:24.1.0")
    // Phone only: home-screen widgets (widgets/).
    "phoneImplementation"("androidx.glance:glance-appwidget:1.1.1")
    // Kiosk + TV: runs the openWakeWord models ("Hey Dana") on-device.
    "kioskImplementation"("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
    "tvImplementation"("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
    // TV only: camera picture-in-picture plays go2rtc's RTSP restream.
    "tvImplementation"("androidx.media3:media3-exoplayer:1.4.1")
    "tvImplementation"("androidx.media3:media3-exoplayer-rtsp:1.4.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
    testImplementation("org.json:json:20240303")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
