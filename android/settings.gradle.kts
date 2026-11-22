pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}
plugins {
    // Lets Gradle download the JDK 21 the build runs on when the machine has none
    // (the toolchainUrl.* entries in gradle/gradle-daemon-jvm.properties come from
    // `./gradlew updateDaemonJvm --jvm-version=21`).
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "Dana"
include(":app")
include(":wear")
include(":protocol")
include(":watchface")
