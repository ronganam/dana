package io.github.ronganam.dana.data.update

import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** Latest published version of one artifact ("phone" or "wear") as reported by the server. */
data class AppVersionInfo(
    val artifact: String,
    val versionName: String,
    val versionCode: Int,
    val file: String,
    val sha256: String,
    val size: Long,
    val releaseNotes: String
)

/**
 * Checks the Dana server for new app versions and performs the
 * download + system-installer handoff.
 *
 * Server endpoints (the server's core/app_updates.py):
 *   GET {server}/api/app/version          -> {"phone": {...}, "wear": {...}}
 *   GET {server}/api/app/download/{file}  -> the APK
 */
object UpdateManager {
    private const val TAG = "UpdateManager"
    private val client get() = io.github.ronganam.dana.data.network.HttpClients.download

    /** Fetch the manifest; returns the artifact entry or null on any failure. */
    suspend fun checkForUpdate(serverUrl: String, artifact: String = "phone"): AppVersionInfo? =
        withContext(Dispatchers.IO) {
            try {
                val base = serverUrl.trimEnd('/')
                if (base.isEmpty() || base == "http://" || base == "https://") return@withContext null
                val request = Request.Builder().url("$base/api/app/version").build()
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        Log.d(TAG, "version check HTTP ${response.code} for $base")
                        return@withContext null
                    }
                    val body = response.body?.string() ?: return@withContext null
                    val obj = JSONObject(body)
                    val entry = obj.optJSONObject(artifact) ?: return@withContext null
                    AppVersionInfo(
                        artifact = artifact,
                        versionName = entry.optString("versionName"),
                        versionCode = entry.optInt("versionCode", 0),
                        file = entry.optString("file"),
                        sha256 = entry.optString("sha256"),
                        size = entry.optLong("size", 0L),
                        releaseNotes = entry.optString("release_notes")
                    )
                }
            } catch (e: Exception) {
                Log.w(TAG, "Update check failed: ${e.message}")
                null
            }
        }

    /** True when the server has a strictly newer version than [currentVersionCode]. */
    fun isNewer(info: AppVersionInfo, currentVersionCode: Int): Boolean =
        info.versionCode > currentVersionCode && info.file.isNotEmpty()

    /**
     * Download the APK into cacheDir/updates and verify its sha256.
     * Returns the verified file, or null on failure.
     */
    suspend fun downloadUpdate(
        context: Context,
        serverUrl: String,
        info: AppVersionInfo,
        onProgress: ((percent: Int) -> Unit)? = null
    ): File? = withContext(Dispatchers.IO) {
        try {
            val base = serverUrl.trimEnd('/')
            val request = Request.Builder()
                .url("$base/api/app/download/${info.file}")
                .build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    Log.w(TAG, "APK download HTTP ${response.code}")
                    return@withContext null
                }
                val body = response.body ?: return@withContext null
                val updatesDir = File(context.cacheDir, "updates").apply { mkdirs() }
                val target = File(updatesDir, info.file)
                val digest = MessageDigest.getInstance("SHA-256")
                val total = body.contentLength().let { if (it > 0) it else info.size }
                var written = 0L
                body.byteStream().use { input ->
                    target.outputStream().use { output ->
                        val buffer = ByteArray(64 * 1024)
                        var lastPercent = -1
                        while (true) {
                            val read = input.read(buffer)
                            if (read == -1) break
                            output.write(buffer, 0, read)
                            digest.update(buffer, 0, read)
                            written += read
                            if (total > 0 && onProgress != null) {
                                val percent = ((written * 100) / total).toInt().coerceAtMost(100)
                                if (percent != lastPercent) {
                                    lastPercent = percent
                                    onProgress(percent)
                                }
                            }
                        }
                    }
                }
                val actualSha = digest.digest().joinToString("") { "%02x".format(it) }
                if (info.sha256.isNotEmpty() && !actualSha.equals(info.sha256, ignoreCase = true)) {
                    Log.w(TAG, "SHA-256 mismatch: expected ${info.sha256}, got $actualSha — deleting")
                    target.delete()
                    return@withContext null
                }
                Log.i(TAG, "Downloaded ${info.file} ($written bytes), sha256 verified")
                target
            }
        } catch (e: Exception) {
            Log.e(TAG, "APK download failed", e)
            null
        }
    }

    /**
     * Hand the downloaded APK to the system package installer.
     * Requires REQUEST_INSTALL_PACKAGES; the user confirms the install on the
     * system dialog. Returns false when the intent cannot be resolved.
     */
    fun installApk(context: Context, apkFile: File): Boolean {
        return try {
            val uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                apkFile
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to launch installer for ${apkFile.name}", e)
            false
        }
    }

    /** Delete all previously downloaded APKs (safe to call any time). */
    fun clearDownloadedUpdates(context: Context) {
        try {
            File(context.cacheDir, "updates").listFiles()?.forEach { it.delete() }
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't clear downloaded updates", e)
        }
    }
}
