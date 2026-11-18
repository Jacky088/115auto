plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// 签名策略：配置 ANDROID_KEYSTORE_BASE64 等 Secret 后用正式密钥签名（升级可覆盖安装）；
// 未配置时回退调试签名（首次使用可先出包验证，正式发布前建议配好 Secret）
val hasReleaseKeystore = !System.getenv("ANDROID_KEYSTORE_BASE64").isNullOrBlank()

android {
    namespace = "io.github.jacky088.autosave115"
    compileSdk = 34

    defaultConfig {
        applicationId = "io.github.jacky088.autosave115"
        minSdk = 24
        targetSdk = 34
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "1.5.1"
    }

    signingConfigs {
        if (hasReleaseKeystore) {
            create("ci") {
                storeFile = file("keystore.keystore") // CI 步骤已把 Secret 解码到此路径
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (hasReleaseKeystore) signingConfigs.getByName("ci")
                            else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.9.3")
}
