package io.github.ronganam.dana.data.shopping

import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.OptString
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * The shopping list as the server sends it (`GET /api/shopping` and every
 * write's answer; server `docs/shopping.md` → API). Read with ApiJson;
 * [ShoppingJson.screen] is the one entry.
 */
data class Who(val id: String, val name: String)

data class ListPerson(val id: String, val name: String, val access: String)

/** [access]: `owner`, `edit` or `read`. [version] rises with every change of the list. */
data class ShoppingList(
    val id: String,
    val name: String,
    val access: String,
    val owner: Who?,
    val people: List<ListPerson>,
    val version: Long
) {
    val canEdit: Boolean get() = access != "read"
    val isOwner: Boolean get() = access == "owner"
}

/** An aisle: the server's key and its name, in store order. */
data class Aisle(val key: String, val name: String)

data class ShoppingItem(
    val id: String,
    val name: String,
    val quantity: String = "",
    val note: String = "",
    val category: String = "other",
    val checked: Boolean = false,
    val checkedAt: String? = null,
    val checkedBy: Who? = null,
    val addedBy: Who? = null,
    val createdAt: String? = null,
    /** Shown before the server has it (an add on its way or queued). Never sent. */
    val pending: Boolean = false
)

data class Usual(val name: String, val category: String, val times: Int)

/** A list someone shared with the caller that is waiting for an answer. */
data class Invite(val listId: String, val name: String, val owner: Who?, val access: String)

/** A list the caller can see; [active] = where their shopping requests go. */
data class ListRef(val id: String, val name: String, val owner: Who?, val access: String, val active: Boolean)

data class Counts(val toBuy: Int, val bought: Int)

data class ShoppingScreen(
    val list: ShoppingList,
    val categories: List<Aisle>,
    /** To buy in display order, then bought (newest first). */
    val items: List<ShoppingItem>,
    val counts: Counts,
    val usuals: List<Usual>,
    val invites: List<Invite>,
    val lists: List<ListRef>
) {
    /** Whether this screen is the caller's active list (the one the disk cache keeps). */
    val isActive: Boolean get() = lists.isEmpty() || lists.any { it.id == list.id && it.active }
}

data class UndoRef(val id: String, val label: String)

/** One item of an add's answer: `added`, `restored` or `already`. */
data class AddResult(val id: String, val name: String, val status: String)

/** A write's answer: the screen of the list written to, its undo and (adds) the results. */
data class WriteAnswer(val screen: ShoppingScreen, val undo: UndoRef?, val results: List<AddResult>)

/** The Share sheet's data (`GET / PUT /api/sharing/shopping_list/{id}`). */
data class SharePayload(val sharedWith: List<ListPerson>, val users: List<Who>)

/** Why the server refused a shopping request (`error`, its `message`, `max` for `too_many`). */
@Serializable
data class ShoppingRefusal(val error: String = "", val message: String = "", val max: Int? = null)

@Serializable
private data class WhoWire(val id: String = "", val name: String = "") {
    fun toModel(): Who? = if (id.isEmpty()) null else Who(id, name)
}

@Serializable
private data class PersonWire(val id: String = "", val name: String = "", val access: String = "")

@Serializable
private data class ListWire(
    val id: String = "", val name: String = "", val access: String = "", val owner: WhoWire? = null,
    val people: LenientList<PersonWire> = emptyList(), val version: Long = 0
)

@Serializable
private data class ItemWire(
    val id: String = "", val name: String = "", val quantity: String = "", val note: String = "", val category: String = "",
    val checked: Boolean = false, val checkedAt: OptString = null, val checkedBy: WhoWire? = null, val addedBy: WhoWire? = null,
    val createdAt: OptString = null
) {
    fun toModel() = ShoppingItem(
        id, name, quantity, note, category.ifEmpty { "other" }, checked, checkedAt, checkedBy?.toModel(), addedBy?.toModel(), createdAt
    )
}

@Serializable
private data class AisleWire(val key: String = "", val name: String = "")

@Serializable
private data class UsualWire(val name: String = "", val category: String = "", val times: Int = 0) {
    fun toModel() = Usual(name, category, times)
}

@Serializable
private data class InviteWire(val listId: String = "", val name: String = "", val owner: WhoWire? = null, val access: String = "")

@Serializable
private data class ListRefWire(val id: String = "", val name: String = "", val owner: WhoWire? = null, val access: String = "", val active: Boolean = false)

@Serializable
private data class CountsWire(val toBuy: Int? = null, val bought: Int? = null)

@Serializable
private data class UndoWire(val id: String = "", val label: String = "")

@Serializable
private data class AddResultWire(val id: String = "", val name: String = "", val status: String = "")

@Serializable
private data class ScreenWire(
    val list: ListWire? = null,
    val categories: LenientList<AisleWire> = emptyList(),
    val items: LenientList<ItemWire> = emptyList(),
    val counts: CountsWire? = null,
    val usuals: LenientList<UsualWire> = emptyList(),
    val invites: LenientList<InviteWire> = emptyList(),
    val lists: LenientList<ListRefWire> = emptyList(),
    val undo: UndoWire? = null,
    val results: LenientList<AddResultWire> = emptyList(),
    val suggestions: LenientList<UsualWire> = emptyList()
) {
    fun toScreen(): ShoppingScreen? {
        val l = list ?: return null
        if (l.id.isEmpty()) return null
        val shown = items.map { it.toModel() }.filter { it.id.isNotEmpty() }
        return ShoppingScreen(
            list = ShoppingList(l.id, l.name, l.access.ifEmpty { "owner" }, l.owner?.toModel(), l.people.map { ListPerson(it.id, it.name, it.access) }, l.version),
            categories = categories.map { Aisle(it.key, it.name) },
            items = shown,
            counts = Counts(counts?.toBuy ?: shown.count { !it.checked }, counts?.bought ?: shown.count { it.checked }),
            usuals = usuals.map { it.toModel() },
            invites = invites.map { Invite(it.listId, it.name, it.owner?.toModel(), it.access) },
            lists = lists.map { ListRef(it.id, it.name, it.owner?.toModel(), it.access, it.active) }
        )
    }
}

@Serializable
private data class SharedWithWire(val userId: String = "", val name: String = "", val level: String = "")

@Serializable
private data class ShareWire(val sharedWith: LenientList<SharedWithWire> = emptyList(), val users: LenientList<WhoWire> = emptyList())

/** Reading the shopping payloads (text or an already-parsed JSON tree). */
object ShoppingJson {
    private fun wire(text: String): ScreenWire? =
        runCatching { ApiJson.decodeFromString(ScreenWire.serializer(), text.ifBlank { "{}" }) }.getOrNull()

    private fun wire(element: JsonElement): ScreenWire? =
        runCatching { ApiJson.decodeFromJsonElement(ScreenWire.serializer(), element) }.getOrNull()

    /** The screen payload; null when it has no `list` (an error body, an old server) or doesn't read. */
    fun screen(text: String): ShoppingScreen? = wire(text)?.toScreen()

    fun screen(element: JsonElement): ShoppingScreen? = wire(element)?.toScreen()

    /** A write's answer: its screen, `undo` and `results`. */
    fun answer(text: String): WriteAnswer? {
        val w = wire(text) ?: return null
        val screen = w.toScreen() ?: return null
        return WriteAnswer(screen, w.undo?.takeIf { it.id.isNotEmpty() }?.let { UndoRef(it.id, it.label) }, w.results.map { AddResult(it.id, it.name, it.status) })
    }

    /** `GET /api/shopping/suggestions` → `suggestions`. */
    fun usuals(text: String): List<Usual> = wire(text)?.suggestions.orEmpty().map { it.toModel() }

    fun share(text: String): SharePayload {
        val w = runCatching { ApiJson.decodeFromString(ShareWire.serializer(), text.ifBlank { "{}" }) }.getOrDefault(ShareWire())
        return SharePayload(w.sharedWith.map { ListPerson(it.userId, it.name, it.level) }, w.users.mapNotNull { it.toModel() })
    }

    /** An error body; null when it isn't JSON. */
    fun refusal(text: String): ShoppingRefusal? =
        runCatching { ApiJson.decodeFromString(ShoppingRefusal.serializer(), text) }.getOrNull()

    /** The error fields of an object already read. */
    fun refusal(o: JsonObject): ShoppingRefusal = runCatching { ApiJson.decodeFromJsonElement(ShoppingRefusal.serializer(), o) }.getOrDefault(ShoppingRefusal())
}
