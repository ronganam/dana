package app.ganam.assistant.wear.data

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

class WearPrefs(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("GanamWearPrefs", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString("server_url", "https://") ?: "https://"
        set(value) = prefs.edit().putString("server_url", value).apply()

    var apiToken: String
        get() = prefs.getString("api_token", "") ?: ""
        set(value) = prefs.edit().putString("api_token", value).apply()

    var sessionId: String
        get() {
            var sid = prefs.getString("chat_session_id", "") ?: ""
            if (sid.isEmpty()) {
                sid = UUID.randomUUID().toString()
                prefs.edit().putString("chat_session_id", sid).apply()
            }
            return sid
        }
        set(value) = prefs.edit().putString("chat_session_id", value).apply()

    var cachedEvents: String
        get() = prefs.getString("cached_events", "[]") ?: "[]"
        set(value) = prefs.edit().putString("cached_events", value).apply()

    var cachedTasks: String
        get() = prefs.getString("cached_tasks", "[]") ?: "[]"
        set(value) = prefs.edit().putString("cached_tasks", value).apply()

    /** When the watch conversation last had activity (speak, send, reply). */
    var lastConversationActivityAt: Long
        get() = prefs.getLong("last_conversation_activity_at", 0L)
        set(value) = prefs.edit().putLong("last_conversation_activity_at", value).apply()

    /** Phone app version reported by the phone via /ganam_config (0 = unknown). */
    var phoneVersionCode: Int
        get() = prefs.getInt("phone_version_code", 0)
        set(value) = prefs.edit().putInt("phone_version_code", value).apply()

    private fun parseIsoDate(start: String): Date? {
        if (start.isEmpty()) return null
        val fmts = listOf(
            "yyyy-MM-dd'T'HH:mm:ss'Z'",
            "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
            "yyyy-MM-dd'T'HH:mm:ssZ",
            "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
            "yyyy-MM-dd'T'HH:mm:ssXXX",
            "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"
        )
        for (fmt in fmts) {
            try {
                val sdf = SimpleDateFormat(fmt, Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
                val parsed = sdf.parse(start)
                if (parsed != null) return parsed
            } catch (_: java.text.ParseException) {
                // Not this format: try the next one.
            }
        }
        return null
    }

    private fun getDayDiffFromNow(timeMs: Long): Int {
        return try {
            val calStart = java.util.Calendar.getInstance().apply {
                timeInMillis = timeMs
                set(java.util.Calendar.HOUR_OF_DAY, 0)
                set(java.util.Calendar.MINUTE, 0)
                set(java.util.Calendar.SECOND, 0)
                set(java.util.Calendar.MILLISECOND, 0)
            }
            val nowStart = java.util.Calendar.getInstance().apply {
                set(java.util.Calendar.HOUR_OF_DAY, 0)
                set(java.util.Calendar.MINUTE, 0)
                set(java.util.Calendar.SECOND, 0)
                set(java.util.Calendar.MILLISECOND, 0)
            }
            val diffMs = calStart.timeInMillis - nowStart.timeInMillis
            (diffMs / (24L * 60 * 60 * 1000)).toInt()
        } catch (_: Exception) { 0 }
    }

    private fun formatEventTime(start: String): String {
        val parsed = parseIsoDate(start) ?: return ""
        return try {
            val cal = java.util.Calendar.getInstance()
            cal.timeInMillis = parsed.time
            val localH = cal.get(java.util.Calendar.HOUR_OF_DAY)
            val localMin = cal.get(java.util.Calendar.MINUTE)
            val dayDiff = getDayDiffFromNow(parsed.time)
            val timePart = when {
                localMin == 0 -> "$localH"
                localMin % 10 == 0 -> "$localH:${localMin / 10}"
                else -> String.format("%d:%02d", localH, localMin)
            }
            if (dayDiff > 0) "${timePart}+$dayDiff" else timePart
        } catch (_: Exception) { "" }
    }

    private fun parseIsoTime(s: String): Long? {
        val formats = listOf(
            "yyyy-MM-dd'T'HH:mm:ss'Z'",
            "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
            "yyyy-MM-dd'T'HH:mm:ssZ",
            "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
            "yyyy-MM-dd'T'HH:mm:ssXXX",
            "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"
        )
        for (fmt in formats) {
            try {
                val sdf = SimpleDateFormat(fmt, Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
                val date = sdf.parse(s) ?: continue
                return date.time
            } catch (_: java.text.ParseException) {
                // Not this format: try the next one.
            }
        }
        return null
    }

    private fun getFilteredEvents(): JSONArray {
        val result = JSONArray()
        try {
            val array = JSONArray(cachedEvents)
            val now = Date().time
            val graceMs = 30 * 60 * 1000L
            for (i in 0 until array.length()) {
                val obj = array.getJSONObject(i)
                if (obj.optBoolean("all_day", false)) continue
                val endStr = obj.optString("end", "")
                if (endStr.isNotEmpty()) {
                    val endMs = parseIsoTime(endStr)
                    if (endMs != null && now > endMs + graceMs) continue
                }
                result.put(obj)
            }
        } catch (e: org.json.JSONException) {
            android.util.Log.w("WearPrefs", "Cached events aren't valid JSON", e)
        }
        return result
    }

    private fun formatTaskDate(dueDate: String): String {
        return try {
            if (dueDate.isNotEmpty() && dueDate.contains("-")) {
                val parts = dueDate.split("-")
                if (parts.size == 3) "${parts[1]}/${parts[2]}" else dueDate
            } else ""
        } catch (_: Exception) { "" }
    }

    fun getEventTitle(index: Int): String {
        return try {
            val array = getFilteredEvents()
            if (index < array.length()) {
                val obj = array.getJSONObject(index)
                val summary = obj.optString("summary", "")
                val cleaned = summary
                    .replace("Canceled: ", "")
                    .replace("Daily Vikings / Core / SRE", "Vikings")
                    .replace("SRE Overview", "SRE")
                    .trim()
                if (cleaned.length > 18) cleaned.substring(0, 16) + ".." else cleaned
            } else "--"
        } catch (_: Exception) { "--" }
    }

    fun getEventTime(index: Int): String {
        return try {
            val array = getFilteredEvents()
            if (index < array.length()) {
                val obj = array.getJSONObject(index)
                formatEventTime(obj.optString("start", ""))
            } else "--"
        } catch (_: Exception) { "--" }
    }

    fun getEventDayDiff(index: Int): Int {
        return try {
            val array = getFilteredEvents()
            if (index < array.length()) {
                val obj = array.getJSONObject(index)
                val start = obj.optString("start", "")
                val parsed = parseIsoDate(start) ?: return 0
                getDayDiffFromNow(parsed.time)
            } else 0
        } catch (_: Exception) { 0 }
    }

    fun getTaskTitle(index: Int): String {
        return try {
            val array = JSONArray(cachedTasks)
            if (index < array.length()) {
                val obj = array.getJSONObject(index)
                val title = obj.optString("title", "")
                if (title.length > 18) title.substring(0, 16) + ".." else title
            } else "--"
        } catch (_: Exception) { "--" }
    }

    fun getTaskDue(index: Int): String {
        return try {
            val array = JSONArray(cachedTasks)
            if (index < array.length()) {
                val obj = array.getJSONObject(index)
                val dueDate = obj.optString("due_date", "")
                if (dueDate.isNotEmpty() && dueDate.contains("-")) {
                    val parts = dueDate.split("-")
                    if (parts.size == 3) "${parts[1]}/${parts[2]}" else dueDate
                } else "--"
            } else "--"
        } catch (_: Exception) { "--" }
    }

    fun isConfigured(): Boolean {
        val url = serverUrl
        return url.isNotEmpty() && url != "https://" && url != "http://"
    }
}
