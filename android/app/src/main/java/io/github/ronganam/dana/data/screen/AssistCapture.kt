package io.github.ronganam.dana.data.screen

import android.app.assist.AssistContent
import android.app.assist.AssistStructure
import android.content.Context
import android.graphics.Bitmap
import android.os.Build
import android.text.InputType
import android.util.Log
import android.view.View
import java.io.ByteArrayOutputStream

/** A screenshot of the app underneath: a small bitmap for the sheet and the JPEG that is sent. */
class ScreenShot(val thumb: Bitmap, val jpeg: ByteArray, val width: Int, val height: Int)

/**
 * Turns what the system delivered to the assistant session into
 * [DeviceScreen] / [ScreenShot]. Runs off the main thread (a large structure
 * is fetched from the app in pieces over binder).
 */
object AssistCapture {
    private const val TAG = "AssistCapture"
    /** The sent screenshot's longest side (px): enough to read, a few hundred KB. */
    private const val SHOT_MAX_SIDE = 1280
    private const val SHOT_QUALITY = 80
    private const val THUMB_MAX_SIDE = 320

    /** The package of the activity the structure came from (null when unknown). */
    fun packageOf(structure: AssistStructure?): String? =
        runCatching { structure?.activityComponent?.packageName }.getOrNull()

    fun describe(context: Context, structure: AssistStructure?, content: AssistContent?): DeviceScreen? {
        if (structure == null && content == null) return null
        return try {
            val pkg = packageOf(structure)
            val groups = ArrayList<List<String>>()
            var title: String? = null
            var web: AssistStructure.ViewNode? = null
            if (structure != null) {
                for (i in 0 until structure.windowNodeCount) {
                    val window = structure.getWindowNodeAt(i)
                    val root = window.rootViewNode ?: continue
                    if (title == null) title = ScreenText.cleanTitle(window.title, pkg)
                    if (web == null) web = findWeb(root)
                    groups.add(ScreenText.lines(toNode(root, Budget()), window.width, window.height))
                }
            }
            val text = ScreenText.text(groups).ifBlank { null }
            val url = content?.webUri?.toString()?.takeIf { it.isNotBlank() }
                ?: web?.webDomain?.let { "${web.webScheme() ?: "https"}://$it" }
            // A browser's window has no useful title; the page's root node carries
            // it (Chrome: the first line, "Espresso - Wikipedia").
            if (title == null) title = web?.let { firstText(it) }
            if (title == null && url != null) title = text?.substringBefore('\n')?.takeIf { it.length in 2..300 }
            DeviceScreen(
                app = pkg?.let { appLabel(context, it) },
                packageName = pkg,
                title = title,
                url = url,
                text = text
            )
        } catch (e: Exception) {
            // A structure the app half-filled must not break the sheet.
            Log.w(TAG, "Could not read the screen's structure", e)
            null
        }
    }

    private class Budget(var left: Int = ScreenText.MAX_NODES)

    private fun toNode(v: AssistStructure.ViewNode, budget: Budget): ScreenNode {
        budget.left--
        val children = ArrayList<ScreenNode>(v.childCount)
        for (i in 0 until v.childCount) {
            if (budget.left <= 0) break
            val child = v.getChildAt(i) ?: continue
            children.add(toNode(child, budget))
        }
        return ScreenNode(
            text = v.text?.toString(),
            contentDescription = v.contentDescription?.toString(),
            visible = v.visibility == View.VISIBLE && v.alpha > 0f,
            left = v.left,
            top = v.top,
            width = v.width,
            height = v.height,
            scrollX = v.scrollX,
            scrollY = v.scrollY,
            password = isPassword(v),
            children = children
        )
    }

    private fun isPassword(v: AssistStructure.ViewNode): Boolean {
        val type = v.inputType
        val variation = type and InputType.TYPE_MASK_VARIATION
        val cls = type and InputType.TYPE_MASK_CLASS
        if (cls == InputType.TYPE_CLASS_TEXT && (
                variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
                    variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD ||
                    variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                )
        ) return true
        if (cls == InputType.TYPE_CLASS_NUMBER && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD) return true
        // Web forms say it in their HTML attributes.
        val html = v.htmlInfo
        return html?.attributes?.any { it.first == "type" && it.second == "password" } == true
    }

    /** The root node of web content (the first with a web domain), if any. */
    private fun findWeb(v: AssistStructure.ViewNode, depth: Int = 0): AssistStructure.ViewNode? {
        if (!v.webDomain.isNullOrBlank()) return v
        if (depth > 40) return null
        for (i in 0 until v.childCount) {
            findWeb(v.getChildAt(i) ?: continue, depth + 1)?.let { return it }
        }
        return null
    }

    /** The page title: the first text at the top of the web content. */
    private fun firstText(v: AssistStructure.ViewNode, depth: Int = 0): String? {
        ScreenText.clean(v.text)?.let { return it }
        if (depth >= 2 || v.childCount == 0) return null
        return v.getChildAt(0)?.let { firstText(it, depth + 1) }
    }

    private fun AssistStructure.ViewNode.webScheme(): String? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) webScheme?.takeIf { it.isNotBlank() } else null

    private fun appLabel(context: Context, pkg: String): String? = runCatching {
        val pm = context.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString().takeIf { it.isNotBlank() && it != pkg }
    }.getOrNull()

    /** Scale and encode the system's screenshot (it may be a hardware bitmap). */
    fun encode(screenshot: Bitmap): ScreenShot? = try {
        val soft = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && screenshot.config == Bitmap.Config.HARDWARE) {
            screenshot.copy(Bitmap.Config.ARGB_8888, false)
        } else screenshot
        val sent = scaled(soft, SHOT_MAX_SIDE)
        val jpeg = ByteArrayOutputStream().use { out ->
            sent.compress(Bitmap.CompressFormat.JPEG, SHOT_QUALITY, out)
            out.toByteArray()
        }
        val thumb = scaled(sent, THUMB_MAX_SIDE)
        val pixels = IntArray(thumb.width * thumb.height)
        thumb.getPixels(pixels, 0, thumb.width, 0, 0, thumb.width, thumb.height)
        if (BlankFrame.isBlank(pixels, thumb.width, thumb.height)) {
            Log.d(TAG, "Screenshot is blank: the app blocks capture")
            null
        } else ScreenShot(thumb, jpeg, soft.width, soft.height)
    } catch (e: Exception) {
        Log.w(TAG, "Could not encode the screenshot", e)
        null
    }

    private fun scaled(b: Bitmap, maxSide: Int): Bitmap {
        val longest = maxOf(b.width, b.height)
        if (longest <= maxSide) return b
        val f = maxSide.toFloat() / longest
        return Bitmap.createScaledBitmap(b, (b.width * f).toInt().coerceAtLeast(1), (b.height * f).toInt().coerceAtLeast(1), true)
    }
}
