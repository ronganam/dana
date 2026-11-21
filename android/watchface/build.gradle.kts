plugins {
    id("com.android.application")
}

android {
    namespace = "app.ganam.assistant.watchface"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.ganam.assistant.watchface"
        minSdk = 30
        targetSdk = 34
        versionCode = 3
        versionName = "1.2"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
}

dependencies {
    compileOnly("com.google.android.wearable:wearable:2.9.0")
}
