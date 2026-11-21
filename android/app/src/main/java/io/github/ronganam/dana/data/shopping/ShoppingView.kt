package io.github.ronganam.dana.data.shopping


/**
 * What the Shopping screen, the widget and the watch show, worked out from a
 * screen payload: rows by aisle, Bought, the quiet line, invitations, the
 * menu, the Share sheet, the editor's rules and every sentence. The same
 * words as the web panel (`server/static/shopping-core.js`). Pure, so the
 * unit tests check it against the server's real payloads.
 */
object ShoppingView {

    data class Row(
        val id: String,
        val name: String,
        val quantity: String,
        val note: String,
        val category: String,
        val checked: Boolean,
        val pending: Boolean,
        val lingering: Boolean,
        /** Who added it, when the list is shared and it wasn't the viewer. */
        val by: Who?
    )

    data class Group(val key: String, val name: String, val rows: List<Row>)

    data class Model(
        val listId: String,
        val canEdit: Boolean,
        val isOwner: Boolean,
        val shared: Boolean,
        val subtitle: String,
        val people: List<ListPerson>,
        val groups: List<Group>,
        val bought: List<Row>,
        val toBuy: Int,
        val usuals: List<Usual>,
        val invites: List<Invite>,
        val empty: Boolean
    )

    /**
     * Everything the screen draws. [linger]: ticked rows that stay in their
     * aisle a moment, each with the row that followed it when it was ticked
     * (null: it was the last of its aisle).
     */
    fun model(screen: ShoppingScreen, me: Who?, linger: Map<String, String?> = emptyMap()): Model {
        val list = screen.list
        val shared = list.people.size > 1
        fun row(it: ShoppingItem) = Row(
            id = it.id, name = it.name, quantity = it.quantity, note = it.note, category = it.category,
            checked = it.checked, pending = it.pending, lingering = it.id in linger,
            by = it.addedBy?.takeIf { by -> shared && (me == null || by.id != me.id) }
        )
        val inPlace = inPlace(screen.items, linger)
        val known = screen.categories.map { it.key }.toSet()
        val groups = screen.categories.mapNotNull { c ->
            val rows = inPlace.filter { it.category == c.key || (c.key == "other" && it.category !in known) }.map(::row)
            if (rows.isEmpty()) null else Group(c.key, c.name, rows)
        }.toMutableList()
        if ("other" !in known) {
            val strays = inPlace.filter { it.category !in known }.map(::row)
            if (strays.isNotEmpty()) groups += Group("other", "Other", strays)
        }
        val bought = screen.items.filter { it.checked && it.id !in linger }.map(::row)
        return Model(
            listId = list.id,
            canEdit = list.canEdit,
            isOwner = list.isOwner,
            shared = shared,
            subtitle = subtitle(screen, me),
            people = if (shared) list.people else emptyList(),
            groups = groups,
            bought = bought,
            toBuy = screen.counts.toBuy,
            usuals = if (list.canEdit) screen.usuals else emptyList(),
            invites = screen.invites,
            empty = groups.isEmpty()
        )
    }

    /**
     * The items shown in their aisles: to buy, plus the just-ticked ones that
     * linger where they were (before the row that followed them), so a tick
     * doesn't make the row jump.
     */
    fun inPlace(items: List<ShoppingItem>, linger: Map<String, String?>): List<ShoppingItem> {
        val out = items.filter { !it.checked }.toMutableList()
        for (it in items.filter { it.checked && it.id in linger }) {
            val anchor = linger[it.id]?.let { a -> out.indexOfFirst { o -> o.id == a && o.category == it.category } } ?: -1
            val lastOfAisle = out.indexOfLast { o -> o.category == it.category }
            when {
                anchor >= 0 -> out.add(anchor, it)
                lastOfAisle >= 0 -> out.add(lastOfAisle + 1, it)
                else -> out.add(it)
            }
        }
        return out
    }

    /** The row that follows [itemId] in its aisle now (what a lingering tick stays before). */
    fun anchorAfter(screen: ShoppingScreen, itemId: String): String? {
        val toBuy = screen.items.filter { !it.checked }
        val i = toBuy.indexOfFirst { it.id == itemId }
        if (i < 0) return null
        return toBuy.getOrNull(i + 1)?.takeIf { it.category == toBuy[i].category }?.id
    }

    /** The short line under the title. */
    fun subtitle(screen: ShoppingScreen, me: Who?): String {
        val list = screen.list
        val n = screen.counts.toBuy
        val count = if (n > 0) "$n to buy" else "Nothing to buy"
        val owner = ShoppingText.firstName(list.owner?.name)
        if (list.access == "read") return "View only · $owner's list"
        if (list.access == "edit") return "$owner's list · $count"
        val others = list.people.filter { it.id != me?.id && it.access != "owner" }.map { ShoppingText.firstName(it.name) }
        if (others.isEmpty()) return count
        val who = when (others.size) {
            1 -> others[0]
            2 -> "${others[0]} and ${others[1]}"
            else -> "${others[0]} and ${others.size - 1} others"
        }
        return "$count · Shared with $who"
    }

    private fun plural(n: Int, one: String, many: String = one + "s") = "$n ${if (n == 1) one else many}"

    /** What an add did. [obvious]: only "Added …" (nothing came back or was already there). */
    data class AddText(val text: String, val obvious: Boolean)

    fun addResultText(results: List<AddResult>): AddText {
        fun by(status: String) = results.filter { it.status == status }.map { it.name }
        val added = by("added"); val back = by("restored"); val already = by("already")
        val out = mutableListOf<String>()
        if (added.isNotEmpty()) out += if (added.size == 1) "Added ${added[0]}" else "Added ${added.size} items"
        if (back.isNotEmpty()) out += if (back.size == 1) "${back[0]} is back on the list" else "${back.size} items are back on the list"
        if (already.isNotEmpty()) out += if (already.size == 1) "${already[0]} is already on the list" else "${already.size} were already on the list"
        return AddText(out.joinToString(" · "), back.isEmpty() && already.isEmpty())
    }

    /** A refused or failed request in plain words. [status] -1: no answer. */
    fun errorText(status: Int, body: ShoppingRefusal?): String {
        val error = body?.error.orEmpty()
        val message = body?.message.orEmpty()
        return when {
            status == -1 -> "You're offline — nothing was changed."
            error == "read_only" -> "This list is view only."
            error == "not_connected" -> "Shopping isn't turned on for you. An admin can connect it in Settings → Integrations."
            error == "too_many" -> "At most ${body?.max ?: ShoppingText.MAX_ITEMS} items at a time."
            error == "duplicate" -> message.ifEmpty { "That's already on the list." }.replace(Regex("^'([^']+)'"), "$1")
            status == 404 -> "That's no longer on the list — someone may have changed it."
            error == "owner_only" -> "Only the list's owner can share it."
            status == 400 -> message.ifEmpty { "That didn't look right." }
            status >= 500 -> "Something went wrong on the server. Try again."
            status == 401 || status == 403 -> "Not authorized (check the API token)."
            else -> message.ifEmpty { error }.ifEmpty { "Couldn't do that ($status)." }
        }
    }

    /** What an undo answered: done, or why not. */
    fun undoText(status: Int, body: ShoppingRefusal?): Pair<Boolean, String> = when (status) {
        in 200..299 -> true to "Undone"
        409 -> false to "Couldn't undo: ${body?.error.orEmpty().ifEmpty { "it changed since" }.trimEnd('.')}."
        404 -> false to "This can no longer be undone."
        else -> false to errorText(status, body)
    }

    /** A change's snackbar from the server's undo label ("Bought 'Eggs'" → "Bought Eggs"). */
    fun undoLabel(label: String): String =
        label.ifEmpty { "Done" }.replace(Regex(" (to|from|on) the shopping list$"), "").replace(Regex("'([^']+)'"), "$1")

    /** The snackbar for a change that is waiting to sync (no server label yet). */
    fun queuedLabel(op: ShoppingOp): String = when (op) {
        is ShoppingOp.Add -> if (op.parts.size == 1) "Added ${op.parts[0].name}" else "Added ${op.parts.size} items"
        is ShoppingOp.Check -> if (op.checked) "Bought ${op.name}" else "${op.name} is back on the list"
        is ShoppingOp.Edit -> "Changed ${op.name}"
        is ShoppingOp.Remove -> "Removed ${op.name}"
        is ShoppingOp.ClearBought -> "Cleared the bought items"
        is ShoppingOp.Forget -> "${op.name} won't be suggested"
    } + " · will sync"

    // ---------- the menu, invitations, sharing ----------

    data class MenuEntry(val act: String, val listId: String = "", val label: String, val note: String = "", val current: Boolean = false)

    /** The list menu's entries (only what it can offer: switching lists, Clear bought). */
    fun menuItems(screen: ShoppingScreen): List<MenuEntry> {
        val out = mutableListOf<MenuEntry>()
        if (screen.lists.size > 1) for (l in screen.lists) {
            val whose = if (l.access == "owner") "Your list" else "${ShoppingText.firstName(l.owner?.name)}'s list"
            out += MenuEntry(
                act = if (l.access == "read") "view" else "switch", listId = l.id, label = whose,
                note = if (l.access == "read") "View only" else "", current = l.id == screen.list.id
            )
        }
        val bought = screen.items.count { it.checked }
        if (screen.list.canEdit && bought > 0) out += MenuEntry(act = "clear", label = "Clear ${plural(bought, "bought item")}")
        return out
    }

    data class InviteModel(val listId: String, val text: String, val canUse: Boolean, val readOnly: Boolean, val askMove: Boolean, val moveText: String)

    fun invite(invite: Invite, screen: ShoppingScreen?): InviteModel {
        val own = if (screen?.list?.isOwner == true) screen.counts.toBuy else 0
        return InviteModel(
            listId = invite.listId,
            text = "${ShoppingText.firstName(invite.owner?.name)} shared their shopping list with you",
            canUse = invite.access == "edit",
            readOnly = invite.access != "edit",
            askMove = own > 0,
            moveText = "Move your ${plural(own, "item")} over?"
        )
    }

    data class ShareModel(val people: List<ListPerson>, val others: List<Who>)

    fun share(p: SharePayload): ShareModel {
        val ids = p.sharedWith.map { it.id }.toSet()
        return ShareModel(p.sharedWith, p.users.filter { it.id !in ids })
    }

    // ---------- the editor ----------

    data class Draft(val itemId: String, val name: String, val quantity: String, val note: String, val category: String)

    fun draft(item: ShoppingItem) = Draft(item.id, item.name, item.quantity, item.note, item.category)

    /** Plain numbers (and empty) get the − / + stepper; free text ("1 kg") doesn't. */
    fun isCount(q: String): Boolean = q.isBlank() || q.trim().all { it.isDigit() }

    /** − / + on the quantity: empty counts as 1; going below 2 empties it. */
    fun stepQuantity(quantity: String, delta: Int): String {
        if (!isCount(quantity)) return quantity
        val n = (if (quantity.isBlank()) 1 else quantity.trim().toInt()) + delta
        return if (n <= 1) "" else minOf(n, 999).toString()
    }

    /** What changed in the editor ({} when nothing), or the reason it can't be saved. */
    fun editChanges(item: ShoppingItem, d: Draft): Result<Map<String, String>> {
        val name = ShoppingText.clip(d.name, ShoppingText.MAX_NAME)
        if (name.isEmpty()) return Result.failure(IllegalArgumentException("The name can't be empty."))
        val out = linkedMapOf<String, String>()
        if (name != item.name) out["name"] = name
        val q = ShoppingText.clip(d.quantity, ShoppingText.MAX_QUANTITY)
        if (q != item.quantity) out["quantity"] = q
        val n = ShoppingText.clip(d.note, ShoppingText.MAX_NOTE)
        if (n != item.note) out["note"] = n
        if (d.category.isNotEmpty() && d.category != item.category) out["category"] = d.category
        return Result.success(out)
    }

    // ---------- the widget and the watch ----------

    /** One item to buy for the widget and the watch, with its aisle's name. */
    data class Line(val id: String, val name: String, val quantity: String, val aisle: String, val aisleKey: String)

    /** The items to buy in the server's order, each with its aisle's name. */
    fun toBuyLines(screen: ShoppingScreen): List<Line> {
        val names = screen.categories.associate { it.key to it.name }
        return screen.items.filter { !it.checked }.map { Line(it.id, it.name, it.quantity, names[it.category] ?: names["other"] ?: "Other", it.category) }
    }
}
