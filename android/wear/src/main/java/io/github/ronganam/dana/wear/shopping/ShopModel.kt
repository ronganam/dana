package io.github.ronganam.dana.wear.shopping

import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.ShopAnswer
import io.github.ronganam.dana.protocol.ShopItem
import io.github.ronganam.dana.protocol.ShopRequest
import io.github.ronganam.dana.protocol.WearJson
import io.github.ronganam.dana.protocol.decodeOrNull
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer

/**
 * The shopping part of the phone's snapshot (`/wear/dana_snapshot`, phone side
 * `data/shopping/ShoppingSnapshot.kt`): what is left to buy in aisle order (at
 * most 40 of [count]), the list it comes from and whether it can be changed.
 */
data class ShopList(val listId: String, val version: Long, val canEdit: Boolean, val count: Int, val items: List<ShopItem>) {
    companion object {
        /** Null when the snapshot has no shopping part (an older phone, nothing loaded yet). */
        fun parse(snapshot: DanaSnapshot?): ShopList? {
            val shopping = snapshot?.shopping ?: return null
            val items = shopping.filter { it.id.isNotEmpty() && it.name.isNotEmpty() }
            return ShopList(
                listId = snapshot.shoppingListId,
                version = snapshot.shoppingVersion,
                canEdit = snapshot.shoppingCanEdit,
                count = maxOf(snapshot.shoppingCount ?: items.size, items.size),
                items = items
            )
        }
    }
}

/** What the watch asks the phone to do (`/shopping_action`). */
sealed class ShopAction {
    /** Tick ([checked]) or untick an item; [item] and [index] put it back in place on the watch. */
    data class Tick(val item: ShopItem, val checked: Boolean, val index: Int) : ShopAction()
    data class Add(val text: String) : ShopAction()
}

/**
 * A request on its way: [Stage.WAITING] kept on the watch (not handed to the
 * phone yet: it is sent when the phone is reachable), [Stage.SENT] handed to
 * the phone, [Stage.ANSWERED] the phone said yes (or never answered in time);
 * it stays applied until the next snapshot, which already has it.
 */
data class Pending(val ref: String, val listId: String, val action: ShopAction, val stage: Stage, val at: Long) {
    enum class Stage { WAITING, SENT, ANSWERED }

    /** The message to the phone. */
    fun payload(): ShopRequest = when (action) {
        is ShopAction.Tick -> ShopRequest(action = if (action.checked) "check" else "uncheck", id = action.item.id, listId = listId, ref = ref)
        is ShopAction.Add -> ShopRequest(action = "add", listId = listId, text = action.text, ref = ref)
    }
}

/** A row that just left, offered back for a few seconds ([index]: where it was). */
data class UndoOffer(val item: ShopItem, val index: Int, val until: Long) {
    val itemId: String get() = item.id
}

/** A short line under the title for a few seconds. [error] lines show in the warning colour. */
data class Notice(val text: String, val until: Long, val error: Boolean = false)

/** A ticked row staying in place for a moment before it leaves. */
data class Linger(val item: ShopItem, val index: Int, val until: Long)

/** Everything the Shopping screen, the tile and the complication show. */
data class ShopState(
    val list: ShopList? = null,
    val pending: List<Pending> = emptyList(),
    val lingering: Map<String, Linger> = emptyMap(),
    val tickedThisVisit: Int = 0,
    val undo: UndoOffer? = null,
    val notice: Notice? = null,
    val unreachable: Boolean = false
)

/** One line of the screen. */
sealed class ShopRow {
    abstract val key: String
    data class Aisle(val name: String, val aisleKey: String) : ShopRow() { override val key = "aisle:$aisleKey:$name" }
    data class Item(val item: ShopItem, val ticked: Boolean) : ShopRow() { override val key = "item:${item.id}" }
}

/**
 * The watch's shopping rules, pure (`ShopModelTest`): the rows with the
 * user's own changes on top of the last snapshot, ticking / unticking / Undo,
 * answers, timeouts, a new snapshot, and the queue's JSON.
 */
object Shop {
    /** A ticked row stays this long before it leaves. */
    const val LINGER_MS = 1_000L
    /** Undo stays offered this long after the row left. */
    const val UNDO_MS = 5_000L
    const val NOTICE_MS = 4_000L
    /** A request the phone took but never answered counts as done after this. */
    const val ANSWER_TIMEOUT_MS = 8_000L

    // ---------- what is shown ----------

    /** The last request for each item still in [pending] (its state on the watch). */
    private fun lastTicks(pending: List<Pending>): Map<String, ShopAction.Tick> {
        val out = LinkedHashMap<String, ShopAction.Tick>()
        for (p in pending) if (p.action is ShopAction.Tick) out[p.action.item.id] = p.action
        return out
    }

    /** The items shown, in order, each with whether it is shown ticked (lingering). */
    fun items(state: ShopState, now: Long): List<ShopRow.Item> {
        val list = state.list ?: return emptyList()
        val last = lastTicks(state.pending)
        val linger = state.lingering.filterValues { it.until > now }
        val inList = list.items.map { it.id }.toSet()
        val rows = ArrayList<ShopRow.Item>()
        for (item in list.items) {
            when {
                item.id in linger -> rows += ShopRow.Item(item, true)
                last[item.id]?.checked == true -> Unit
                else -> rows += ShopRow.Item(item, false)
            }
        }
        // Rows the snapshot no longer has: one still lingering, or one put back (untick / Undo).
        val back = linger.values.filter { it.item.id !in inList }.map { Triple(it.item, it.index, true) } +
            last.values.filter { !it.checked && it.item.id !in inList && it.item.id !in linger }.map { Triple(it.item, it.index, false) }
        for ((item, index, ticked) in back.sortedBy { it.second }) {
            rows.add(index.coerceIn(0, rows.size), ShopRow.Item(item, ticked))
        }
        return rows
    }

    /** [items] with a label where the aisle changes. */
    fun rows(state: ShopState, now: Long): List<ShopRow> {
        val out = ArrayList<ShopRow>()
        var aisle: String? = null
        for (r in items(state, now)) {
            if (r.item.aisleKey != aisle || out.isEmpty()) {
                aisle = r.item.aisleKey
                if (r.item.aisle.isNotEmpty()) out += ShopRow.Aisle(r.item.aisle, r.item.aisleKey)
            }
            out += r
        }
        return out
    }

    /** Items left to buy (the title, the tile, the complication), counting what the snapshot left out. */
    fun toBuy(state: ShopState, now: Long): Int {
        val list = state.list ?: return 0
        val open = items(state, now).count { !it.ticked }
        return (list.count - list.items.size + open).coerceAtLeast(0)
    }

    /** Requests kept on the watch because the phone could not be reached. */
    fun waiting(state: ShopState): Int = state.pending.count { it.stage == Pending.Stage.WAITING }

    /** Nothing left and something was ticked during this visit. */
    fun allDone(state: ShopState, now: Long): Boolean =
        state.list != null && state.tickedThisVisit > 0 && items(state, now).isEmpty()

    // ---------- the user's changes ----------

    /** Tick [item]: shown ticked at once, then it leaves; Undo is offered. */
    fun tick(state: ShopState, item: ShopItem, ref: String, now: Long): ShopState {
        val list = state.list ?: return state
        if (!list.canEdit) return state
        val index = items(state, now).indexOfFirst { it.item.id == item.id }.coerceAtLeast(0)
        return state.copy(
            pending = state.pending + Pending(ref, list.listId, ShopAction.Tick(item, true, index), Pending.Stage.WAITING, now),
            lingering = state.lingering + (item.id to Linger(item, index, now + LINGER_MS)),
            tickedThisVisit = state.tickedThisVisit + 1,
            undo = UndoOffer(item, index, now + LINGER_MS + UNDO_MS)
        )
    }

    /**
     * Put [itemId] back (a second tap while it lingers, or Undo). A tick still
     * waiting on the watch is simply dropped; otherwise an untick is sent (also
     * when the tick was answered and the snapshot already left the item out).
     */
    fun untick(state: ShopState, itemId: String, ref: String, now: Long): ShopState {
        val list = state.list ?: return state
        val lastIndex = state.pending.indexOfLast { (it.action as? ShopAction.Tick)?.item?.id == itemId }
        val last = state.pending.getOrNull(lastIndex)
        val lastTick = last?.action as? ShopAction.Tick
        if (lastTick != null && !lastTick.checked) return state
        val pending = when {
            last != null && last.stage == Pending.Stage.WAITING -> state.pending.filterIndexed { i, _ -> i != lastIndex }
            else -> {
                val (item, index) = lastTick?.let { it.item to it.index }
                    ?: state.lingering[itemId]?.let { it.item to it.index }
                    ?: state.undo?.takeIf { it.itemId == itemId }?.let { it.item to it.index }
                    ?: return state
                state.pending + Pending(ref, last?.listId ?: list.listId, ShopAction.Tick(item, false, index), Pending.Stage.WAITING, now)
            }
        }
        return state.copy(
            pending = pending,
            lingering = state.lingering - itemId,
            tickedThisVisit = (state.tickedThisVisit - 1).coerceAtLeast(0),
            undo = state.undo?.takeIf { it.itemId != itemId }
        )
    }

    /** Add spoken text (the phone splits it like its add field). */
    fun add(state: ShopState, text: String, ref: String, now: Long): ShopState {
        val list = state.list ?: return state
        val t = text.trim()
        if (t.isEmpty() || !list.canEdit) return state
        return state.copy(pending = state.pending + Pending(ref, list.listId, ShopAction.Add(t), Pending.Stage.WAITING, now))
    }

    /** A new visit to the screen: "all done" counts from here. */
    fun startVisit(state: ShopState): ShopState =
        if (state.lingering.isEmpty()) state.copy(tickedThisVisit = 0, undo = null) else state

    // ---------- talking to the phone ----------

    /** The next request to hand to the phone: in order, one at a time. */
    fun next(state: ShopState): Pending? =
        if (state.pending.any { it.stage == Pending.Stage.SENT }) null
        else state.pending.firstOrNull { it.stage == Pending.Stage.WAITING }

    /** The phone took [ref]. */
    fun sent(state: ShopState, ref: String, now: Long): ShopState = state.copy(
        pending = state.pending.map { if (it.ref == ref) it.copy(stage = Pending.Stage.SENT, at = now) else it },
        unreachable = false
    )

    /** The phone could not be reached: everything stays waiting on the watch. */
    fun unreachable(state: ShopState, now: Long): ShopState {
        val adds = state.pending.filter { it.stage == Pending.Stage.WAITING && it.action is ShopAction.Add }
        val notice = if (!state.unreachable && adds.isNotEmpty())
            Notice("Phone not reachable. Will add “${(adds.last().action as ShopAction.Add).text}” when it's back.", now + NOTICE_MS, error = true)
        else state.notice
        return state.copy(unreachable = true, notice = notice)
    }

    /** The phone's answer `{ok, queued, error?, ref}` (`queued` counts as done). */
    fun answered(state: ShopState, answer: ShopAnswer, now: Long): ShopState {
        val ref = answer.ref.orEmpty()
        val p = state.pending.firstOrNull { it.ref == ref } ?: return state
        if (answer.ok) {
            val notice = (p.action as? ShopAction.Add)?.let { Notice("Added: ${it.text}", now + NOTICE_MS) } ?: state.notice
            return state.copy(
                pending = state.pending.map { if (it.ref == ref) it.copy(stage = Pending.Stage.ANSWERED, at = now) else it },
                notice = notice
            )
        }
        // Refused: drop it, so the row comes back where it was, and say why.
        val error = answer.error.orEmpty().ifBlank { "The phone couldn't do that." }
        val tick = p.action as? ShopAction.Tick
        return state.copy(
            pending = state.pending.filter { it.ref != ref },
            lingering = if (tick != null) state.lingering - tick.item.id else state.lingering,
            tickedThisVisit = if (tick?.checked == true) (state.tickedThisVisit - 1).coerceAtLeast(0) else state.tickedThisVisit,
            undo = if (tick != null) state.undo?.takeIf { it.itemId != tick.item.id } else state.undo,
            notice = Notice(error, now + NOTICE_MS, error = true)
        )
    }

    /** A request handed over long ago without an answer counts as done (the phone has it). */
    fun timeouts(state: ShopState, now: Long): ShopState = state.copy(
        pending = state.pending.map {
            if (it.stage == Pending.Stage.SENT && now - it.at >= ANSWER_TIMEOUT_MS) it.copy(stage = Pending.Stage.ANSWERED, at = now) else it
        }
    )

    /**
     * A new snapshot from the phone. It is built after every answer it
     * follows, so answered requests are in it and leave the queue; requests
     * still on their way stay applied on top. The phone is reachable again.
     */
    fun onSnapshot(state: ShopState, list: ShopList?): ShopState = state.copy(
        list = list ?: state.list,
        pending = state.pending.filter { it.stage != Pending.Stage.ANSWERED },
        unreachable = false
    )

    /** Drop what has run out at [now]: lingering rows, the Undo offer, the notice. */
    fun expire(state: ShopState, now: Long): ShopState {
        val linger = state.lingering.filterValues { it.until > now }
        val undo = state.undo?.takeIf { it.until > now }
        val notice = state.notice?.takeIf { it.until > now }
        return if (linger.size == state.lingering.size && undo == state.undo && notice == state.notice) state
        else state.copy(lingering = linger, undo = undo, notice = notice)
    }

    /** When something next changes by itself (null: nothing timed). */
    fun nextDeadline(state: ShopState): Long? =
        (state.lingering.values.map { it.until } + listOfNotNull(state.undo?.until, state.notice?.until) +
            state.pending.filter { it.stage == Pending.Stage.SENT }.map { it.at + ANSWER_TIMEOUT_MS }).minOrNull()

    // ---------- the queue on disk ----------

    /** Requests not yet answered, for the prefs (a sent one counts as answered after a restart). */
    fun queueJson(pending: List<Pending>): String = WearJson.encodeToString(
        QUEUE,
        pending.filter { it.stage != Pending.Stage.ANSWERED }.map { p ->
            when (val a = p.action) {
                is ShopAction.Tick -> QueuedRequest(p.ref, p.listId, p.stage.name, p.at, item = a.item, checked = a.checked, index = a.index)
                is ShopAction.Add -> QueuedRequest(p.ref, p.listId, p.stage.name, p.at, text = a.text)
            }
        }
    )

    fun parseQueue(text: String?): List<Pending> =
        QUEUE.decodeOrNull(text ?: "[]").orEmpty().mapNotNull { q ->
            if (q.ref.isEmpty()) return@mapNotNull null
            val action = q.item?.takeIf { it.id.isNotEmpty() && it.name.isNotEmpty() }?.let { ShopAction.Tick(it, q.checked, q.index) }
                ?: q.text?.takeIf { it.isNotBlank() }?.let { ShopAction.Add(it) }
                ?: return@mapNotNull null
            // A request sent before the app stopped reached the phone: treat it as answered.
            val stage = if (q.stage == Pending.Stage.WAITING.name) Pending.Stage.WAITING else Pending.Stage.ANSWERED
            Pending(q.ref, q.listId, action, stage, q.at)
        }

    private val QUEUE = ListSerializer(QueuedRequest.serializer())

    // ---------- text ----------

    /** True when [s] has more right-to-left letters (Hebrew, Arabic) than left-to-right ones. */
    fun mostlyRtl(s: String): Boolean {
        var rtl = 0
        var ltr = 0
        for (ch in s) {
            when (Character.getDirectionality(ch)) {
                Character.DIRECTIONALITY_RIGHT_TO_LEFT, Character.DIRECTIONALITY_RIGHT_TO_LEFT_ARABIC -> rtl++
                Character.DIRECTIONALITY_LEFT_TO_RIGHT -> ltr++
            }
        }
        return rtl > ltr
    }

    /** The title's quiet line ("" when nothing is left: the screen says so itself). */
    fun subtitle(state: ShopState, now: Long): String {
        val n = toBuy(state, now)
        val count = if (n == 0) "" else "$n to buy"
        return if (state.list?.canEdit == false) listOf("View only", count).filter { it.isNotEmpty() }.joinToString(" · ") else count
    }
}

/** One request of the queue in the prefs: a tick ([item], [checked], [index]) or an add ([text]). */
@Serializable
private data class QueuedRequest(
    val ref: String = "",
    @SerialName("list_id") val listId: String = "",
    val stage: String = "",
    val at: Long = 0,
    val item: ShopItem? = null,
    val checked: Boolean = true,
    val index: Int = 0,
    val text: String? = null,
)
