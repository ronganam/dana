package app.ganam.assistant.data.notifications

import android.content.Context
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.service.notification.StatusBarNotification
import android.util.Log
import android.util.LruCache
import androidx.core.graphics.drawable.toBitmap
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

/**
 * Real app icons for the Inbox.
 *
 * Package visibility hides most apps from a plain lookup, but every captured
 * notification carries its sender's ApplicationInfo ("android.appInfo"), and
 * loading the icon through that needs no visibility. The listener saves each
 * app's icon once (filesDir/app_icons/<package>.png, refreshed weekly) so the
 * Inbox can show it later, also for notifications no longer in the shade.
 */
object AppIcons {
    private const val TAG = "AppIcons"
    private const val SIZE_PX = 108
    private const val REFRESH_MS = 7L * 24 * 3600 * 1000

    private val memory = LruCache<String, Bitmap>(120)
    /** Packages with no icon anywhere (don't retry on every recomposition). */
    private val missing = ConcurrentHashMap.newKeySet<String>()
    private val io = Executors.newSingleThreadExecutor()

    private fun dir(context: Context) = File(context.filesDir, "app_icons").apply { mkdirs() }

    private fun file(context: Context, pkg: String) =
        File(dir(context), pkg.replace(Regex("[^A-Za-z0-9._-]"), "_") + ".png")

    /** From the listener: remember [sbn]'s app icon if it isn't saved yet. Cheap; work runs off the main thread. */
    fun remember(context: Context, sbn: StatusBarNotification) {
        val pkg = sbn.packageName ?: return
        val f = file(context, pkg)
        if (f.exists() && System.currentTimeMillis() - f.lastModified() < REFRESH_MS) return
        @Suppress("DEPRECATION")
        val info = sbn.notification?.extras?.get("android.appInfo") as? ApplicationInfo ?: return
        val app = context.applicationContext
        io.execute {
            try {
                val bmp = app.packageManager.getApplicationIcon(info).toBitmap(SIZE_PX, SIZE_PX)
                save(f, bmp)
                memory.put(pkg, bmp)
                missing.remove(pkg)
            } catch (e: Exception) {
                Log.w(TAG, "Couldn't save the icon of $pkg", e)
            }
        }
    }

    /** Already in memory (no I/O) — for the first frame. */
    fun cached(pkg: String): Bitmap? = memory.get(pkg)

    /** Memory → saved file → direct lookup (visible packages). Call off the main thread. */
    fun load(context: Context, pkg: String): Bitmap? {
        memory.get(pkg)?.let { return it }
        if (pkg in missing) return null
        val f = file(context, pkg)
        if (f.exists()) {
            BitmapFactory.decodeFile(f.path)?.let { memory.put(pkg, it); return it }
        }
        return try {
            val bmp = context.packageManager.getApplicationIcon(pkg).toBitmap(SIZE_PX, SIZE_PX)
            save(f, bmp)
            memory.put(pkg, bmp)
            bmp
        } catch (e: Exception) {
            missing.add(pkg)
            null
        }
    }

    /** Launcher label (lower case) → package, built once; the manifest's LAUNCHER `<queries>` makes these apps visible. */
    @Volatile private var byLabel: Map<String, String>? = null

    /**
     * The package of the launcher app called [label] ("WhatsApp"), for items
     * that only carry the app's name. Null when unknown or ambiguous (two apps
     * with that name). Call off the main thread.
     */
    fun packageForLabel(context: Context, label: String): String? {
        val key = label.trim().lowercase()
        if (key.isEmpty()) return null
        val map = byLabel ?: synchronized(this) {
            byLabel ?: buildLabelMap(context).also { byLabel = it }
        }
        return map[key]
    }

    private fun buildLabelMap(context: Context): Map<String, String> {
        val pm = context.packageManager
        val intent = android.content.Intent(android.content.Intent.ACTION_MAIN).addCategory(android.content.Intent.CATEGORY_LAUNCHER)
        val seen = HashMap<String, String?>()
        try {
            pm.queryIntentActivities(intent, 0).forEach { ri ->
                val name = ri.loadLabel(pm).toString().trim().lowercase()
                val pkg = ri.activityInfo?.packageName ?: return@forEach
                if (name.isEmpty()) return@forEach
                // The same package twice (several launcher activities) is fine; two packages is ambiguous.
                seen[name] = if (!seen.containsKey(name) || seen[name] == pkg) pkg else null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't list launcher apps", e)
        }
        return seen.filterValues { it != null }.mapValues { it.value!! }
    }

    private fun save(f: File, bmp: Bitmap) {
        val tmp = File(f.path + ".tmp")
        tmp.outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
        tmp.renameTo(f)
    }
}
