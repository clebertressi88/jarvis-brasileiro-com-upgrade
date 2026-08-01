plugins {
    id("com.android.application")
}

android {
    namespace = "br.com.jarvis.remote"
    compileSdk = 36

    defaultConfig {
        applicationId = "br.com.jarvis.remote"
        minSdk = 26
        targetSdk = 36
        versionCode = 4
        versionName = "1.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:5.3.0")
    testImplementation("junit:junit:4.13.2")
}
