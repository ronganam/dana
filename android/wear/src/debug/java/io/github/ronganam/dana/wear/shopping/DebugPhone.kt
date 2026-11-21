package io.github.ronganam.dana.wear.shopping

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.ShopAnswer
import io.github.ronganam.dana.protocol.ShopItem
import io.github.ronganam.dana.protocol.ShopRequest
import io.github.ronganam.dana.wear.cards.SnapshotStore
import java.util.UUID

/**
 * Debug builds only: a stand-in phone for the watch's Shopping screen when no
 * phone is paired (emulators). Off unless switched on with [DebugShoppingReceiver]:
 *
 *   adb -s <watch> shell am broadcast -n io.github.ronganam.dana/.wear.shopping.DebugShoppingReceiver \
 *       --es phone ok|unreachable|refuse|off  --es snapshot_b64 <base64 JSON>  --es say_b64 <base64 text>
 *
 * `ok` answers like the phone (`{ok, queued: false, ref}` after 300 ms) and
 * sends a new snapshot 1.5 s later with the change applied; `unreachable`
 * refuses every send; `refuse` answers `{ok: false, error}`. `snapshot_b64`
 * stores a snapshot as if the phone had sent it; `say_b64` adds text as if it
 * had been spoken.
 */
object DebugPhone {
    private const val PREFS = "debug_phone"
    private val main = Handler(Looper.getMainLooper())
    /** Items ticked through the fake phone, to put back on an untick. */
    private val removed = HashMap<String, Pair<ShopItem, Int>>()

    fun mode(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("mode", "off") ?: "off"

    fun setMode(context: Context, mode: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("mode", mode).apply()
    }

    fun link(context: Context): ShoppingLink? = when (mode(context)) {
        "ok" -> ShoppingLink { c, payload -> answerLater(c, payload, ok = true); true }
        "refuse" -> ShoppingLink { c, payload -> answerLater(c, payload, ok = false); true }
        "unreachable" -> ShoppingLink { _, _ -> false }
        else -> null
    }

    private fun answerLater(context: Context, payload: ShopRequest, ok: Boolean) {
        val app = context.applicationContext
        main.postDelayed({
            val answer = if (ok) ShopAnswer(ok = true, queued = false, ref = payload.ref)
            else ShopAnswer(ok = false, error = "This list is view only.", ref = payload.ref)
            WatchShopping.onAnswer(answer)
            if (ok) main.postDelayed({ SnapshotStore.save(app, applied(SnapshotStore.load(app) ?: DanaSnapshot(), payload)) }, 1_500)
        }, 300)
    }

    /** [snapshot] with the request applied, as the phone's next snapshot would have it. */
    private fun applied(snapshot: DanaSnapshot, payload: ShopRequest): DanaSnapshot {
        val items = snapshot.shopping.orEmpty()
        val list = items.toMutableList()
        val id = payload.id.orEmpty()
        when (payload.action) {
            "check" -> {
                val i = list.indexOfFirst { it.id == id }
                if (i >= 0) removed[id] = list.removeAt(i) to i
            }
            "uncheck" -> removed.remove(id)?.let { (o, i) -> list.add(i.coerceIn(0, list.size), o) }
            "add" -> payload.text.orEmpty().split(',', '\n').map { it.trim() }.filter { it.isNotEmpty() }.forEach {
                list.add(ShopItem(id = UUID.randomUUID().toString().take(12), name = it, aisle = "Other", aisleKey = "other"))
            }
        }
        val count = (snapshot.shoppingCount ?: items.size) + (list.size - items.size)
        return snapshot.copy(shopping = list, shoppingCount = count.coerceAtLeast(0), shoppingVersion = snapshot.shoppingVersion + 1)
    }
}

/** Debug builds only: drives [DebugPhone] from adb (see there). */
class DebugShoppingReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        intent.getStringExtra("phone")?.let { DebugPhone.setMode(app, it) }
        intent.getStringExtra("snapshot_b64")?.let { b64 ->
            val snapshot = DanaSnapshot.decode(String(Base64.decode(b64, Base64.DEFAULT)))
            if (snapshot == null) Log.w("DebugPhone", "Bad snapshot") else SnapshotStore.save(app, snapshot)
        }
        intent.getStringExtra("say_b64")?.let { WatchShopping.add(app, String(Base64.decode(it, Base64.DEFAULT))) }
        Log.i("DebugPhone", "phone=${DebugPhone.mode(app)}")
    }
}
