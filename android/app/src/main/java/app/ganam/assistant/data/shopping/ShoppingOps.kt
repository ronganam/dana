package app.ganam.assistant.data.shopping

import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.UUID

/**
 * One change the user made to a list, from the moment it is tapped until the
 * server has it. The screen always shows the server's last answer with every
 * change still on its way applied on top ([applyOps]); a refused change just
 * drops out, so the screen falls back by itself. Changes are also what the
 * offline queue stores ([encode] / [decode]).
 *
 * [id] names the change (Undo of a queued change), [listId] the list it was
 * made on (writes always send `?list=`).
 */
sealed class ShoppingOp {
    abstract val id: String
    abstract val listId: String

    /** Items to add, each with the client's own id (a repeated request adds nothing twice). */
    data class Add(override val id: String, override val listId: String, val parts: List<AddPart>) : ShoppingOp()
    data class Check(override val id: String, override val listId: String, val itemId: String, val checked: Boolean, val name: String) : ShoppingOp()
    /** [changes]: any of name, quantity, note, category. */
    data class Edit(override val id: String, override val listId: String, val itemId: String, val changes: Map<String, String>, val name: String) : ShoppingOp()
    data class Remove(override val id: String, override val listId: String, val itemId: String, val name: String) : ShoppingOp()
    data class ClearBought(override val id: String, override val listId: String) : ShoppingOp()
    data class Forget(override val id: String, override val listId: String, val name: String) : ShoppingOp()

    companion object {
        fun newId(): String = UUID.randomUUID().toString()
    }
}

data class AddPart(val itemId: String, val name: String, val quantity: String = "", val note: String = "")

/** A request: method, path (with `?list=`) and JSON body (null: none). */
data class OpRequest(val method: String, val path: String, val body: JSONObject?)

object ShoppingOps {

    private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    private fun q(listId: String) = if (listId.isEmpty()) "" else "?list=${enc(listId)}"

    /** What [op] sends. Adds always go as `{items: [{name, quantity, id}]}` (client ids). */
    fun request(op: ShoppingOp): OpRequest = when (op) {
        is ShoppingOp.Add -> OpRequest("POST", "/api/shopping/items${q(op.listId)}", JSONObject().put("items", JSONArray(op.parts.map { p ->
            JSONObject().put("id", p.itemId).put("name", p.name).apply {
                if (p.quantity.isNotEmpty()) put("quantity", p.quantity)
                if (p.note.isNotEmpty()) put("note", p.note)
            }
        })))
        is ShoppingOp.Check -> OpRequest("PATCH", "/api/shopping/items/${enc(op.itemId)}${q(op.listId)}", JSONObject().put("checked", op.checked))
        is ShoppingOp.Edit -> OpRequest("PATCH", "/api/shopping/items/${enc(op.itemId)}${q(op.listId)}", JSONObject(op.changes as Map<*, *>))
        is ShoppingOp.Remove -> OpRequest("DELETE", "/api/shopping/items/${enc(op.itemId)}${q(op.listId)}", null)
        is ShoppingOp.ClearBought -> OpRequest("POST", "/api/shopping/clear-bought${q(op.listId)}", null)
        is ShoppingOp.Forget -> OpRequest("DELETE", "/api/shopping/suggestions${q(op.listId)}", JSONObject().put("name", op.name))
    }

    /** The add for typed text (null: nothing in it). More than 30 items is the caller's to refuse. */
    fun addTyped(listId: String, text: String): ShoppingOp.Add? {
        val parts = ShoppingText.parse(text)
        if (parts.isEmpty()) return null
        return ShoppingOp.Add(ShoppingOp.newId(), listId, parts.map { AddPart(ShoppingOp.newId(), it.name, it.quantity) })
    }

    /** The add for one chosen name (a usual, a suggestion): never parsed for a count. */
    fun addName(listId: String, name: String): ShoppingOp.Add =
        ShoppingOp.Add(ShoppingOp.newId(), listId, listOf(AddPart(ShoppingOp.newId(), ShoppingText.clip(name, ShoppingText.MAX_NAME))))

    // ---------- applying changes on screen ----------

    /** Items in display order: to buy by aisle (then as they came), bought newest first. */
    fun ordered(items: List<ShoppingItem>, categories: List<Aisle>): List<ShoppingItem> {
        val rank = categories.withIndex().associate { it.value.key to it.index }
        val other = rank["other"] ?: 99
        val toBuy = items.withIndex().filter { !it.value.checked }
            .sortedWith(compareBy({ rank[it.value.category] ?: other }, { it.index })).map { it.value }
        val bought = items.filter { it.checked }.sortedByDescending { it.checkedAt.orEmpty() }
        return toBuy + bought
    }

    /** The aisle a name is known under on this screen (an item or a usual with that name), else other. */
    private fun knownAisle(screen: ShoppingScreen, key: String): String =
        screen.items.firstOrNull { ShoppingText.nameKey(it.name) == key && it.category != "other" }?.category
            ?: screen.usuals.firstOrNull { ShoppingText.nameKey(it.name) == key }?.category?.takeIf { it.isNotEmpty() }
            ?: "other"

    /** One change applied to a screen's items (the same rules as the server, so the screen doesn't jump). */
    fun applyOp(screen: ShoppingScreen, op: ShoppingOp, me: Who?, now: String): ShoppingScreen {
        var items = screen.items
        var usuals = screen.usuals
        fun update(id: String, f: (ShoppingItem) -> ShoppingItem) { items = items.map { if (it.id == id) f(it) else it } }
        when (op) {
            is ShoppingOp.Add -> for (p in op.parts) {
                val key = ShoppingText.nameKey(p.name)
                val open = items.firstOrNull { !it.checked && ShoppingText.nameKey(it.name) == key }
                if (open != null) {
                    if (p.quantity.isNotEmpty() || p.note.isNotEmpty()) update(open.id) {
                        it.copy(quantity = p.quantity.ifEmpty { it.quantity }, note = p.note.ifEmpty { it.note })
                    }
                    continue
                }
                if (items.any { it.id == p.itemId }) continue  // a repeated request
                val done = items.firstOrNull { it.checked && ShoppingText.nameKey(it.name) == key }
                if (done != null) {
                    items = items.filter { it !== done } + done.copy(checked = false, checkedAt = null, checkedBy = null, quantity = p.quantity, note = p.note)
                    continue
                }
                items = items + ShoppingItem(
                    id = p.itemId, name = p.name, quantity = p.quantity, note = p.note, category = knownAisle(screen, key),
                    addedBy = me, createdAt = now, pending = true
                )
            }
            is ShoppingOp.Check -> update(op.itemId) {
                if (op.checked) it.copy(checked = true, checkedAt = now, checkedBy = me) else it.copy(checked = false, checkedAt = null, checkedBy = null)
            }
            is ShoppingOp.Edit -> update(op.itemId) {
                it.copy(
                    name = op.changes["name"] ?: it.name,
                    quantity = op.changes["quantity"] ?: it.quantity,
                    note = op.changes["note"] ?: it.note,
                    category = op.changes["category"] ?: it.category
                )
            }
            is ShoppingOp.Remove -> items = items.filter { it.id != op.itemId }
            is ShoppingOp.ClearBought -> items = items.filter { !it.checked }
            is ShoppingOp.Forget -> usuals = usuals.filter { ShoppingText.nameKey(it.name) != ShoppingText.nameKey(op.name) }
        }
        return screen.copy(items = items, usuals = usuals)
    }

    /** The screen as shown: the server's last answer with every change of this list on top. */
    fun applyOps(screen: ShoppingScreen?, ops: List<ShoppingOp>, me: Who?, now: String): ShoppingScreen? {
        if (screen == null) return null
        val mine = ops.filter { it.listId == screen.list.id }
        if (mine.isEmpty()) return screen
        var s: ShoppingScreen = screen
        for (op in mine) s = applyOp(s, op, me, now)
        val items = ordered(s.items, s.categories)
        val toBuy = items.filter { !it.checked }
        val keys = toBuy.map { ShoppingText.nameKey(it.name) }.toSet()
        return s.copy(
            items = items,
            counts = Counts(toBuy.size, items.size - toBuy.size),
            usuals = s.usuals.filter { ShoppingText.nameKey(it.name) !in keys }
        )
    }

    // ---------- versions ----------

    /** Whether an answer (a write's, a GET) replaces the screen held. */
    fun accepts(current: ShoppingScreen?, incoming: ShoppingScreen?): Boolean {
        if (incoming == null) return false
        if (current == null) return true
        if (incoming.list.id != current.list.id) return true
        return incoming.list.version >= current.list.version
    }

    enum class Reload { NOW, LATER, NO }

    /** What a `changed` event means: read the list now, after the own writes land, or not at all. */
    fun onChanged(listId: String, version: Long, current: ShoppingScreen?, writesOnTheirWay: Int): Reload = when {
        current == null || listId != current.list.id -> Reload.NOW
        version <= current.list.version -> Reload.NO
        writesOnTheirWay > 0 -> Reload.LATER
        else -> Reload.NOW
    }

    // ---------- the queue on disk ----------

    fun encode(op: ShoppingOp): JSONObject {
        val o = JSONObject().put("id", op.id).put("list_id", op.listId)
        when (op) {
            is ShoppingOp.Add -> o.put("type", "add").put("parts", JSONArray(op.parts.map {
                JSONObject().put("item_id", it.itemId).put("name", it.name).put("quantity", it.quantity).put("note", it.note)
            }))
            is ShoppingOp.Check -> o.put("type", "check").put("item_id", op.itemId).put("checked", op.checked).put("name", op.name)
            is ShoppingOp.Edit -> o.put("type", "edit").put("item_id", op.itemId).put("changes", JSONObject(op.changes as Map<*, *>)).put("name", op.name)
            is ShoppingOp.Remove -> o.put("type", "remove").put("item_id", op.itemId).put("name", op.name)
            is ShoppingOp.ClearBought -> o.put("type", "clear")
            is ShoppingOp.Forget -> o.put("type", "forget").put("name", op.name)
        }
        return o
    }

    /** One stored change, or null for anything unreadable (skipped, never fatal). */
    fun decode(o: JSONObject): ShoppingOp? {
        val id = o.optString("id")
        val listId = o.optString("list_id")
        if (id.isEmpty() || listId.isEmpty()) return null
        val itemId = o.optString("item_id")
        val name = o.optString("name")
        return when (o.optString("type")) {
            "add" -> {
                val a = o.optJSONArray("parts") ?: return null
                val parts = (0 until a.length()).mapNotNull { a.optJSONObject(it) }.mapNotNull { p ->
                    val pid = p.optString("item_id"); val pname = p.optString("name")
                    if (pid.isEmpty() || pname.isEmpty()) null else AddPart(pid, pname, p.optString("quantity"), p.optString("note"))
                }
                if (parts.isEmpty()) null else ShoppingOp.Add(id, listId, parts)
            }
            "check" -> if (itemId.isEmpty()) null else ShoppingOp.Check(id, listId, itemId, o.optBoolean("checked"), name)
            "edit" -> {
                val c = o.optJSONObject("changes") ?: return null
                if (itemId.isEmpty()) null else ShoppingOp.Edit(id, listId, itemId, c.keys().asSequence().associateWith { c.optString(it) }, name)
            }
            "remove" -> if (itemId.isEmpty()) null else ShoppingOp.Remove(id, listId, itemId, name)
            "clear" -> ShoppingOp.ClearBought(id, listId)
            "forget" -> if (name.isEmpty()) null else ShoppingOp.Forget(id, listId, name)
            else -> null
        }
    }

    fun encodeAll(ops: List<ShoppingOp>): String = JSONArray(ops.map(::encode)).toString()

    fun decodeAll(text: String): List<ShoppingOp> {
        val a = runCatching { JSONArray(text) }.getOrNull() ?: return emptyList()
        return (0 until a.length()).mapNotNull { a.optJSONObject(it) }.mapNotNull(::decode)
    }

    /** Answers worth trying again later: no answer at all, a timeout, too many requests, a server error. */
    fun isRetryable(code: Int): Boolean = code == -1 || code == 408 || code == 429 || code >= 500
}
