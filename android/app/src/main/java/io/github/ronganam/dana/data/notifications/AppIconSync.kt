package io.github.ronganam.dana.data.notifications

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.DevicesApi
import io.github.ronganam.dana.data.network.Me
import io.github.ronganam.dana.data.network.ServerConfig
import kotlinx.coroutines.CancellationException
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** The decisions of the app-icon upload, without Android (unit-tested). */
object AppIconSyncPlan {
    /** At most one sync this often, unless a new package appeared. */
    const val INTERVAL_MS = 6L * 3600 * 1000

    private val PACKAGE = Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+$")

    /** Whether [pkg] is a package name the server takes. */
    fun validPackage(pkg: String): Boolean = pkg.length <= 200 && PACKAGE.matches(pkg)

    /** The server's hash of an icon: the first 8 hex chars of the PNG's sha256. */
    fun hashOf(png: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(png).take(4).joinToString("") { "%02x".format(it) }

    /** The packages to upload: those the server lacks or holds with another hash, sorted. */
    fun toUpload(local: Map<String, String>, server: Map<String, String>): List<String> =
        local.filter { (pkg, hash) -> server[pkg] != hash }.keys.sorted()

    /**
     * Whether a sync should run now: never synced, the last one [INTERVAL_MS]
     * or more ago (or in the future: the clock moved), or a saved package
     * that the last sync didn't see.
     */
    fun due(nowMs: Long, lastSyncMs: Long, local: Set<String>, synced: Set<String>): Boolean =
        lastSyncMs <= 0 || nowMs - lastSyncMs >= INTERVAL_MS || nowMs < lastSyncMs || !synced.containsAll(local)
}

/** How one sync ended. */
sealed interface AppIconSyncResult {
    /** The server has every icon it can take; [uploaded] were sent this time. */
    data class Done(val uploaded: List<String>) : AppIconSyncResult

    /** The phone addon isn't connected for this user (403). */
    data object NotConnected : AppIconSyncResult

    /** The server couldn't be reached or failed; try again next time. */
    data object Failed : AppIconSyncResult
}

/**
 * Sends the app icons [AppIcons] saved to the server (`/api/phone/app-icons`),
 * so the web shows the same icons: lists what the server has, then uploads
 * each icon it lacks or holds with another hash. Runs in [AppIconSyncWorker]
 * after an icon is saved and once when the listener connects, at most once
 * per [AppIconSyncPlan.INTERVAL_MS] unless a new package appeared; a failure
 * waits quietly for the next run.
 */
object AppIconSync {
    private const val TAG = "AppIconSync"
    private const val WORK_NAME = "app-icon-sync"

    /** Ask for a sync (the worker decides whether one is due). Cheap, any thread. */
    fun request(context: Context) {
        val request = OneTimeWorkRequestBuilder<AppIconSyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            // Icons often come in a burst (the shade at start): one run for all of them.
            .setInitialDelay(20, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, request)
    }

    /**
     * One sync against [api]: [local] is package → hash of what [png] returns
     * for it. A 409 (`too_many`) stops the uploads; a 400 / 413 skips that
     * icon (it will be offered again next time).
     */
    suspend fun upload(api: DevicesApi, local: Map<String, String>, png: (String) -> ByteArray?): AppIconSyncResult {
        return try {
            val server = api.appIcons()
            val uploaded = mutableListOf<String>()
            for (pkg in AppIconSyncPlan.toUpload(local, server)) {
                val bytes = png(pkg) ?: continue
                val code = api.putAppIcon(pkg, bytes)
                when {
                    code in 200..299 -> uploaded += pkg
                    code == 403 -> return AppIconSyncResult.NotConnected
                    code == 409 -> break
                    code >= 500 -> return AppIconSyncResult.Failed
                    else -> Log.w(TAG, "The server refused the icon of $pkg (HTTP $code)")
                }
            }
            AppIconSyncResult.Done(uploaded)
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (e.code == 403) AppIconSyncResult.NotConnected else AppIconSyncResult.Failed
        } catch (e: IOException) {
            AppIconSyncResult.Failed
        }
    }

    /** Run a sync when one is due (the worker's body). */
    internal suspend fun runIfDue(context: Context) {
        val config = ServerConfig.load(context)
        if (!config.isConfigured) return
        if (Me.cached(context)?.has(ADDON) == false) return
        val packages = AppIcons.savedPackages(context)
        val state = State.read(context)
        val now = System.currentTimeMillis()
        if (!AppIconSyncPlan.due(now, state.at, packages, state.packages)) return
        val hashes = packages.mapNotNull { pkg -> AppIcons.uploadPng(context, pkg)?.let { pkg to AppIconSyncPlan.hashOf(it) } }.toMap()
        when (val r = upload(config.api().devices, hashes) { AppIcons.uploadPng(context, it) }) {
            is AppIconSyncResult.Done -> {
                if (r.uploaded.isNotEmpty()) Log.i(TAG, "Uploaded ${r.uploaded.size} app icons")
                State(now, packages).write(context)
            }
            AppIconSyncResult.NotConnected -> {
                Me.drop(context, ADDON)
                State(now, packages).write(context)
            }
            AppIconSyncResult.Failed -> Log.i(TAG, "App icon sync failed; trying again next time")
        }
    }

    private const val ADDON = "phone"

    /** When the last sync ended and the packages it saw (filesDir/app_icons_synced.txt: the time, then one package a line). */
    private data class State(val at: Long, val packages: Set<String>) {
        fun write(context: Context) {
            val f = file(context)
            val tmp = File(f.path + ".tmp")
            tmp.writeText((listOf(at.toString()) + packages.sorted()).joinToString("\n"))
            tmp.renameTo(f)
        }

        companion object {
            fun file(context: Context) = File(context.filesDir, "app_icons_synced.txt")

            fun read(context: Context): State {
                val lines = try {
                    file(context).takeIf { it.exists() }?.readLines().orEmpty()
                } catch (e: IOException) {
                    emptyList()
                }
                return State(lines.firstOrNull()?.toLongOrNull() ?: 0L, lines.drop(1).filter { it.isNotBlank() }.toSet())
            }
        }
    }
}

/** Runs [AppIconSync] off the main thread, with a network. */
class AppIconSyncWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        AppIconSync.runIfDue(applicationContext)
        return Result.success()
    }
}
