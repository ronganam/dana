import java.util.Properties

plugins {
    id("com.android.application")
}

// The Dana watch face: a Watch Face Format face (resources only, no code). Its slots
// default to the watch app's complications (io.github.ronganam.dana), signed with the
// same release key as the other apps.
val keystoreProperties = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "io.github.ronganam.dana.watchface"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.ronganam.dana.watchface"
        minSdk = 30
        targetSdk = 34
        versionCode = 4
        versionName = "1.3"
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
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
                ?: signingConfigs.getByName("debug")
        }
    }
}
