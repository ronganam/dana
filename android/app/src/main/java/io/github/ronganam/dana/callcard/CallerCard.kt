package io.github.ronganam.dana.callcard

import io.github.ronganam.dana.data.network.ServerConfig
import android.annotation.SuppressLint
import androidx.compose.ui.graphics.toArgb
import io.github.ronganam.dana.ui.theme.AccentDark
import io.github.ronganam.dana.ui.theme.AccentLight
import io.github.ronganam.dana.ui.theme.DarkInk
import io.github.ronganam.dana.ui.theme.DarkInkSoft
import io.github.ronganam.dana.ui.theme.DarkMute
import io.github.ronganam.dana.ui.theme.DarkOutlineSoft
import io.github.ronganam.dana.ui.theme.DarkSurface
import io.github.ronganam.dana.ui.theme.LightInk
import io.github.ronganam.dana.ui.theme.LightInkSoft
import io.github.ronganam.dana.ui.theme.LightMute
import io.github.ronganam.dana.ui.theme.LightOutlineSoft
import io.github.ronganam.dana.ui.theme.LightSurface
import io.github.ronganam.dana.ui.theme.ThemeMode
import io.github.ronganam.dana.ui.theme.ThemeSettings
import io.github.ronganam.dana.ui.theme.DanaHues
import io.github.ronganam.dana.ui.theme.Hue
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.telephony.TelephonyManager
import android.text.SpannableString
import android.text.Spanned
import android.text.TextUtils
import android.text.style.ForegroundColorSpan
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import io.github.ronganam.dana.EXTRA_OPEN_PERSON_ID
import io.github.ronganam.dana.MainActivity
import io.github.ronganam.dana.data.models.PersonCard
import io.github.ronganam.dana.data.wear.WearCards
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.PeopleApi
import io.github.ronganam.dana.data.network.long
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import androidx.core.content.edit
import io.github.ronganam.dana.data.sync.RecordingScans

/**
 * Caller card: while someone known calls (or the user calls them), a floating card
 * over the dialer says who they are, when you last spoke, what's going on and
 * what's open between you — from `GET /api/people/lookup` (server
 * docs/people.md; the server answers from its DB, no model). The card is also
 * sent to the watch. A cached copy per number shows instantly and the network
 * answer replaces it.
 *
 * Needs READ_PHONE_STATE + READ_CALL_LOG (the number) and "Display over other
 * apps". Settings → "Caller card" turns it off (pref [KEY_ENABLED]).
 */
class CallerCardReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return
        val app = context.applicationContext
        val state = intent.getStringExtra(TelephonyManager.EXTRA_STATE)
        // A call ended: its recording is about to land in the recordings folder.
        if (state == TelephonyManager.EXTRA_STATE_IDLE) RecordingScans.now(app, RecordingScans.AFTER_CALL_DELAY_SECONDS)
        if (!CallerCards.enabled(app)) return
        @Suppress("DEPRECATION")
        val number = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER)
        val pending = goAsync()
        CallerCards.scope.launch {
            try {
                CallerCards.onState(app, state, number)
            } catch (e: Exception) {
                Log.w(CallerCards.TAG, "Caller card failed", e)
            } finally {
                pending.finish()
            }
        }
    }
}

object CallerCards {
    const val TAG = "CallerCard"
    const val KEY_ENABLED = "caller_card_enabled"
    private const val CACHE = "caller_card_cache"
    private const val CACHE_MAX = 60
    private const val KEY_Y = "caller_card_y"

    internal val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var lastState: String? = null
    private var shownNumber: String? = null

    fun enabled(context: Context): Boolean =
        ServerConfig.prefs(context).getBoolean(KEY_ENABLED, true)

    fun setEnabled(context: Context, on: Boolean) {
        ServerConfig.prefs(context).edit { putBoolean(KEY_ENABLED, on) }
        if (!on) CallerCardOverlay.hide()
    }

    /** What's missing for the card to work (for Settings). */
    fun missing(context: Context): List<String> = buildList {
        if (context.checkSelfPermission(android.Manifest.permission.READ_PHONE_STATE) != android.content.pm.PackageManager.PERMISSION_GRANTED) add("phone state")
        if (context.checkSelfPermission(android.Manifest.permission.READ_CALL_LOG) != android.content.pm.PackageManager.PERMISSION_GRANTED) add("call log")
        if (!Settings.canDrawOverlays(context)) add("display over other apps")
    }

    internal suspend fun onState(context: Context, state: String?, number: String?) {
        val prev = lastState
        when (state) {
            TelephonyManager.EXTRA_STATE_RINGING -> {
                lastState = state
                if (!number.isNullOrBlank()) show(context, number, incoming = true)
            }
            TelephonyManager.EXTRA_STATE_OFFHOOK -> {
                lastState = state
                // Off-hook without ringing first = the user is calling out.
                if (prev != TelephonyManager.EXTRA_STATE_RINGING && !number.isNullOrBlank() && shownNumber == null) {
                    show(context, number, incoming = false)
                } else {
                    CallerCardOverlay.collapse()
                }
            }
            TelephonyManager.EXTRA_STATE_IDLE -> {
                lastState = state
                shownNumber?.let { WearCards.clear(context, "caller") }
                shownNumber = null
                CallerCardOverlay.hide()
            }
        }
    }

    private suspend fun show(context: Context, number: String, incoming: Boolean) {
        if (shownNumber == number) return
        shownNumber = number
        cached(context, number)?.let { CallerCardOverlay.show(context, it, incoming) }
        val card = withTimeoutOrNull(4000) {
            withContext(Dispatchers.IO) {
                runCatching { ServerConfig.load(context).api().people.lookupCaller(number) }
                    .onFailure { Log.d(TAG, "Lookup failed: ${it.message}") }
                    .getOrElse { return@withContext cached(context, number) }
            }
        }
        if (shownNumber != number) return  // the call already ended
        if (card == null) {
            if (cached(context, number) == null) CallerCardOverlay.hide()
            return
        }
        remember(context, number, card)
        CallerCardOverlay.show(context, card, incoming)
        WearCards.send(context, "caller", card.name, card.headline, card.summaryLines(), card.personId)
    }

    private fun cached(context: Context, number: String): PersonCard? {
        val raw = ServerConfig.prefs(context).getString(CACHE, null) ?: return null
        return runCatching { (cache(raw)[key(number)] as? JsonObject)?.let(PeopleApi::card) }.getOrNull()
    }

    /** The cache: number key → the card's JSON plus `_t` (when it was kept). */
    private fun cache(raw: String?): JsonObject =
        runCatching { ApiJson.parseToJsonElement(raw ?: "{}") as? JsonObject }.getOrNull() ?: JsonObject(emptyMap())

    private fun remember(context: Context, number: String, card: PersonCard) {
        val prefs = ServerConfig.prefs(context)
        val all = cache(prefs.getString(CACHE, "{}")).toMutableMap()
        all[key(number)] = JsonObject(PeopleApi.cardJson(card) + ("_t" to JsonPrimitive(System.currentTimeMillis())))
        if (all.size > CACHE_MAX) {  // drop the oldest
            val keys = all.keys.sortedBy { (all[it] as? JsonObject)?.long("_t") ?: 0 }
            keys.take(all.size - CACHE_MAX).forEach { all.remove(it) }
        }
        prefs.edit { putString(CACHE, JsonObject(all).toString()) }
    }

    private fun key(number: String) = number.filter { it.isDigit() }.takeLast(9)

    internal fun savedY(context: Context): Int =
        ServerConfig.prefs(context).getInt(KEY_Y, -1)

    internal fun saveY(context: Context, y: Int) =
        ServerConfig.prefs(context).edit { putInt(KEY_Y, y) }
}

/**
 * The floating card itself: plain Views in a TYPE_APPLICATION_OVERLAY window
 * (no Compose: no lifecycle owner outside an activity). Circuit colours (a tonal card, no border),
 * drag to move (the height is remembered), tap to open the person in Dana,
 * × to close. It never takes focus, so the dialer's buttons keep working.
 */
@SuppressLint("StaticFieldLeak")
object CallerCardOverlay {
    private var root: View? = null
    private var details: View? = null
    private var kickerView: TextView? = null
    private var relation: String = ""
    private var params: WindowManager.LayoutParams? = null

    /** Ink & Paper tokens as View colours, picked per show() from the app's theme setting. */
    private class Palette(val dark: Boolean) {
        val ink = (if (dark) DarkInk else LightInk).toArgb()
        val inkSoft = (if (dark) DarkInkSoft else LightInkSoft).toArgb()
        val mute = (if (dark) DarkMute else LightMute).toArgb()
        val surface = (if (dark) DarkSurface else LightSurface).copy(alpha = 0.96f).toArgb()
        val outline = (if (dark) DarkOutlineSoft else LightOutlineSoft).toArgb()
        val accent = (if (dark) AccentDark else AccentLight).toArgb()
        val open = (if (dark) AccentDark else AccentLight).toArgb()
        private val hues = DanaHues(dark)
        /** The person's hue (as in People): its tint behind the initials, the letters in it. */
        fun personTint(key: String) = hues.tint(hues.forName(key)).toArgb()
        fun personText(key: String) = hues.text(hues.forName(key)).toArgb()
    }

    private fun isDark(context: Context): Boolean {
        ThemeSettings.load(context)
        return when (ThemeSettings.mode) {
            ThemeMode.DARK -> true
            ThemeMode.LIGHT -> false
            ThemeMode.SYSTEM -> (context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        }
    }

    fun show(context: Context, card: PersonCard, incoming: Boolean) {
        if (!Settings.canDrawOverlays(context)) return
        val app = context.applicationContext
        hide()
        val wm = app.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val view = build(app, card, incoming)
        val metrics = app.resources.displayMetrics
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            // Default: a third of the way down, between the caller name and the answer buttons.
            y = CallerCards.savedY(app).takeIf { it >= 0 } ?: (metrics.heightPixels * 0.34f).toInt()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
        try {
            wm.addView(view, lp)
            root = view
            params = lp
        } catch (e: Exception) {
            Log.w(CallerCards.TAG, "Couldn't show the caller card", e)
        }
    }

    /** Once the call is answered: keep just the header. */
    fun collapse() {
        details?.visibility = View.GONE
        kickerView?.text = ("On the call" + relation.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty())
        root?.let { v -> params?.let { runCatching { (v.context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).updateViewLayout(v, it) } } }
    }

    fun hide() {
        val v = root ?: return
        runCatching { (v.context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(v) }
        root = null
        details = null
        kickerView = null
        params = null
    }

    private fun dp(context: Context, v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, context.resources.displayMetrics).toInt()

    private fun text(context: Context, s: CharSequence, size: Float, color: Int, bold: Boolean = false, lines: Int = 2, perLine: Boolean = false) =
        TextView(context).apply {
            text = s
            setTextColor(color)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
            if (bold) typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
            maxLines = lines
            ellipsize = TextUtils.TruncateAt.END
            textDirection = View.TEXT_DIRECTION_FIRST_STRONG
            // Body lines sit at their own script's start (a Hebrew line on the right); the header stays beside the avatar.
            textAlignment = if (perLine) View.TEXT_ALIGNMENT_TEXT_START else View.TEXT_ALIGNMENT_VIEW_START
        }

    /** "○ text": the ring in the warning hue, the words in ink so they read on Paper too. */
    private fun openLine(s: String, ring: Int): CharSequence = SpannableString("○  $s").apply {
        setSpan(ForegroundColorSpan(ring), 0, 1, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    }

    @SuppressLint("ClickableViewAccessibility", "SetTextI18n")
    private fun build(context: Context, card: PersonCard, incoming: Boolean): View {
        val c = Palette(isDark(context))
        val pad = dp(context, 16f)
        val panel = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            background = GradientDrawable().apply {
                cornerRadius = dp(context, 24f).toFloat()
                setColor(c.surface)
            }
            elevation = dp(context, 8f).toFloat()
        }

        val header = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val hueKey = card.personId.ifBlank { card.name }
        val avatar = TextView(context).apply {
            text = card.initials
            gravity = Gravity.CENTER
            setTextColor(c.personText(hueKey))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(c.personTint(hueKey)) }
        }
        header.addView(avatar, LinearLayout.LayoutParams(dp(context, 40f), dp(context, 40f)))
        val titles = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(context, 12f), 0, dp(context, 8f), 0) }
        val kicker = (if (incoming) "Calling" else "Calling out") + (card.relation.takeIf { it.isNotBlank() }?.let { " · $it" } ?: "")
        relation = card.relation
        titles.addView(text(context, kicker, 13f, c.inkSoft, lines = 1).also { kickerView = it })
        titles.addView(text(context, card.name, 18f, c.ink, bold = true, lines = 1))
        titles.addView(text(context, card.headline, 13f, c.inkSoft, lines = 1))
        header.addView(titles, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        val close = text(context, "✕", 18f, c.mute).apply {
            setPadding(dp(context, 10f), dp(context, 4f), dp(context, 4f), dp(context, 4f))
            contentDescription = "Close"
            setOnClickListener { hide() }
        }
        header.addView(close)
        panel.addView(header)

        val body = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(0, dp(context, 10f), 0, 0) }
        val lines = buildList<Pair<CharSequence, Int>> {
            card.points.forEach { add("•  $it" to c.ink) }
            card.open.forEach { add(openLine(it, c.open) to c.ink) }
            if (card.points.isEmpty() && card.lastCallSummary != null) {
                add("Last call${card.lastCallAgo?.let { " ($it)" } ?: ""}: ${card.lastCallSummary}" to c.inkSoft)
            }
            card.birthday?.let { add("🎂  Birthday $it" to c.ink) }
        }
        lines.take(5).forEach { (s, col) -> body.addView(text(context, s, 14f, col, lines = 2, perLine = true).apply { setPadding(0, dp(context, 2f), 0, dp(context, 2f)) }) }
        if (card.pendingSuggestions > 0) {
            body.addView(text(context, if (card.pendingSuggestions == 1) "1 suggestion from your calls is waiting" else "${card.pendingSuggestions} suggestions from your calls are waiting", 12f, c.mute, lines = 1, perLine = true))
        }
        if (lines.isNotEmpty() || card.pendingSuggestions > 0) panel.addView(body)
        details = body

        // Tap opens the person in Dana; drag moves the card.
        var downY = 0f
        var startY = 0
        var moved = false
        panel.setOnTouchListener { v, ev ->
            val lp = params ?: return@setOnTouchListener false
            when (ev.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downY = ev.rawY; startY = lp.y; moved = false; true }
                MotionEvent.ACTION_MOVE -> {
                    val dy = (ev.rawY - downY).toInt()
                    if (kotlin.math.abs(dy) > dp(context, 6f)) moved = true
                    if (moved) {
                        lp.y = (startY + dy).coerceAtLeast(0)
                        runCatching { (context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).updateViewLayout(root, lp) }
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (moved) CallerCards.saveY(context, lp.y) else v.performClick()
                    true
                }
                else -> false
            }
        }
        panel.setOnClickListener {
            val open = Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(EXTRA_OPEN_PERSON_ID, card.personId)
            runCatching { context.startActivity(open) }
            hide()
        }

        return FrameLayout(context).apply {
            val m = dp(context, 12f)
            // Phone width on the cover; a 560dp card centred when unfolded, so a line stays readable.
            val metrics = context.resources.displayMetrics
            val wide = metrics.widthPixels / metrics.density >= 600f
            val width = if (wide) dp(context, 560f) else FrameLayout.LayoutParams.MATCH_PARENT
            addView(panel, FrameLayout.LayoutParams(width, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL).apply {
                setMargins(m, 0, m, 0)
            })
        }
    }
}
