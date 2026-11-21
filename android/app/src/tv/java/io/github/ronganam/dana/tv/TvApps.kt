package io.github.ronganam.dana.tv

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.Drawable
import android.util.Log
import android.util.LruCache
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class TvApp(val pkg: String, val label: String, val intent: Intent)

/** Launchable TV apps and their banners (decoded once, at tile size). */
object TvApps {
    private const val TAG = "TvApps"
    const val BANNER_W = 320
    const val BANNER_H = 180

    private val banners = object : LruCache<String, ImageBitmap>(6 * 1024 * 1024) {
        override fun sizeOf(key: String, value: ImageBitmap) = value.width * value.height * 4
    }

    /** Leanback launcher apps (plus phone-style launcher-only apps), sorted by label, without Dana TV itself. */
    suspend fun list(context: Context): List<TvApp> = withContext(Dispatchers.IO) {
        val pm = context.packageManager
        val seen = LinkedHashMap<String, TvApp>()
        for (category in listOf(Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER)) {
            val query = Intent(Intent.ACTION_MAIN).addCategory(category)
            for (ri in pm.queryIntentActivities(query, 0)) {
                val pkg = ri.activityInfo.packageName
                if (pkg == context.packageName || pkg in seen) continue
                val intent = Intent(Intent.ACTION_MAIN).addCategory(category)
                    .setClassName(pkg, ri.activityInfo.name)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
                seen[pkg] = TvApp(pkg, ri.loadLabel(pm).toString(), intent)
            }
        }
        seen.values.sortedBy { it.label.lowercase() }
    }

    fun launch(context: Context, pkg: String): Boolean {
        val pm = context.packageManager
        val intent = (pm.getLeanbackLaunchIntentForPackage(pkg) ?: pm.getLaunchIntentForPackage(pkg))
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
            ?: return false
        return runCatching { context.startActivity(intent) }
            .onFailure { Log.w(TAG, "Couldn't launch $pkg", it) }.isSuccess
    }

    /** The app's 16:9 TV banner, or its icon centred on a plain tile when it has none. */
    suspend fun banner(context: Context, app: TvApp): ImageBitmap? {
        banners.get(app.pkg)?.let { return it }
        return withContext(Dispatchers.IO) {
            runCatching {
                val pm = context.packageManager
                val banner: Drawable? = pm.getActivityBanner(app.intent) ?: pm.getApplicationBanner(app.pkg)
                val bmp = Bitmap.createBitmap(BANNER_W, BANNER_H, Bitmap.Config.ARGB_8888)
                val canvas = Canvas(bmp)
                if (banner != null) {
                    banner.setBounds(0, 0, BANNER_W, BANNER_H)
                    banner.draw(canvas)
                } else {
                    canvas.drawColor(0xFF26201B.toInt()) // DarkSurfaceHigh
                    val icon = pm.getActivityIcon(app.intent)
                    val s = 96
                    icon.setBounds((BANNER_W - s) / 2, (BANNER_H - s) / 2 - 12, (BANNER_W + s) / 2, (BANNER_H + s) / 2 - 12)
                    icon.draw(canvas)
                }
                bmp.asImageBitmap().also { banners.put(app.pkg, it) }
            }.onFailure { Log.w(TAG, "No banner for ${app.pkg}", it) }.getOrNull()
        }
    }

    /** True when the app has a real banner (the fallback tile needs a label drawn under the icon). */
    fun hasBanner(context: Context, app: TvApp): Boolean = runCatching {
        val pm = context.packageManager
        pm.getActivityBanner(app.intent) != null || pm.getApplicationBanner(app.pkg) != null
    }.getOrDefault(false)
}
