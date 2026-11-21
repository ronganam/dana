package io.github.ronganam.dana.wear.shopping

import io.github.ronganam.dana.protocol.ShopItem
import android.content.Context
import android.net.Uri
import android.os.SystemClock
import android.util.Log
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.ShopAnswer
import io.github.ronganam.dana.wear.cards.SnapshotStore
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.UUID

/**
 * The watch's shopping list for this process: the last snapshot from the
 * phone with the user's own ticks on top ([Shop] holds the rules). Requests go
 * to the phone one at a time, in order. When the phone can't be reached they
 * stay on the watch (`shopping_pending` in the prefs, shown as done) and are
 * sent when it is back: the next snapshot (the phone is there), the screen
 * opening, the next tick, or every [RETRY_MS] while the screen shows.
 * Every change of the state happens on the main thread.
 */
object WatchShopping {
    private const val TAG = "WatchShopping"
    private const val PREFS = "DanaWearPrefs"
    private const val KEY_QUEUE = "shopping_pending"
    private const val RETRY_MS = 10_000L

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val sending = Mutex()
    private val _state = MutableStateFlow(ShopState())
    val state: StateFlow<ShopState> = _state.asStateFlow()

    @Volatile private var app: Context? = null
    private var timer: Job? = null
    private var retry: Job? = null
    private var lastCount = -1

    private val answers = MessageClient.OnMessageReceivedListener { event ->
        if (event.path != ShoppingLink.PATH_ANSWER) return@OnMessageReceivedListener
        ShopAnswer.decode(String(event.data))?.let { scope.launch { onAnswer(it) } }
    }

    fun now(): Long = SystemClock.elapsedRealtime()

    /** Loads the snapshot and the waiting requests once per process and listens for the phone's answers. */
    fun init(context: Context) {
        if (app != null) return
        synchronized(this) {
            if (app != null) return
            val c = context.applicationContext
            app = c
            val queue = Shop.parseQueue(c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_QUEUE, null))
            _state.value = ShopState(list = ShopList.parse(SnapshotStore.load(c)), pending = queue)
            runCatching {
                Wearable.getMessageClient(c).addListener(answers, Uri.parse("wear://*${ShoppingLink.PATH_ANSWER}"), MessageClient.FILTER_LITERAL)
            }.onFailure { Log.w(TAG, "Couldn't listen for answers", it) }
        }
    }

    /** Items left to buy for the tile and the complication (own waiting ticks included). */
    fun toBuy(context: Context): Int {
        init(context)
        return Shop.toBuy(_state.value, now())
    }

    // ---------- the screen ----------

    fun startVisit(context: Context) {
        init(context)
        update { Shop.startVisit(it) }
        pump()
        retry?.cancel()
        retry = scope.launch {
            while (true) {
                delay(RETRY_MS)
                if (Shop.waiting(_state.value) > 0) pump()
            }
        }
    }

    fun endVisit() {
        retry?.cancel()
        retry = null
    }

    fun tick(item: ShopItem) {
        update { Shop.tick(it, item, newRef(), now()) }
        pump()
    }

    fun untick(itemId: String) {
        update { Shop.untick(it, itemId, newRef(), now()) }
        pump()
    }

    fun add(context: Context, text: String) {
        init(context)
        update { Shop.add(it, text, newRef(), now()) }
        pump()
    }

    // ---------- the phone ----------

    /** A snapshot arrived (`/wear/dana_snapshot`): the phone is reachable, so send what waits. */
    fun onSnapshot(context: Context, snapshot: DanaSnapshot) {
        init(context)
        val list = ShopList.parse(snapshot)
        scope.launch {
            update { Shop.onSnapshot(it, list) }
            pump()
        }
    }

    fun onAnswer(answer: ShopAnswer) {
        update { Shop.answered(it, answer, now()) }
        pump()
    }

    /** Hands the waiting requests to the phone, in order, while it takes them. */
    private fun pump() {
        val c = app ?: return
        scope.launch {
            sending.withLock {
                while (true) {
                    val p = Shop.next(_state.value) ?: break
                    val ok = runCatching { ShoppingLink.forProcess(c).send(c, p.payload()) }.getOrDefault(false)
                    if (ok) update { Shop.sent(it, p.ref, now()) } else { update { Shop.unreachable(it, now()) }; break }
                }
            }
        }
    }

    // ---------- state ----------

    private fun update(f: (ShopState) -> ShopState) {
        val before = _state.value
        val after = Shop.expire(Shop.timeouts(f(before), now()), now())
        _state.value = after
        if (after.pending != before.pending) save(after)
        schedule(after)
        val count = Shop.toBuy(after, now())
        if (count != lastCount) {
            lastCount = count
            app?.let { SnapshotStore.refreshSurfaces(it) }
        }
    }

    private fun save(s: ShopState) {
        app?.getSharedPreferences(PREFS, Context.MODE_PRIVATE)?.edit()?.putString(KEY_QUEUE, Shop.queueJson(s.pending))?.apply()
    }

    /** Wakes up when a row stops lingering, Undo or a notice runs out, or a request times out. */
    private fun schedule(s: ShopState) {
        timer?.cancel()
        val at = Shop.nextDeadline(s) ?: return
        timer = scope.launch {
            delay((at - now()).coerceAtLeast(0) + 20)
            update { it }
            pump()
        }
    }

    private fun newRef(): String = "w-" + UUID.randomUUID().toString().take(12)
}
