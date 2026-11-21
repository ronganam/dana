package app.ganam.assistant.data.shopping

import org.json.JSONArray
import org.json.JSONObject

/**
 * The shopping list as the server sends it (`GET /api/shopping` and every
 * write's answer; server repo `docs/shopping.md` → API). Parsed by hand with
 * org.json like the rest of the app; [ShoppingJson.screen] is the one entry.
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

/** Reading and writing the shopping payloads. */
object ShoppingJson {
    private fun JSONObject.str(key: String): String =
        if (isNull(key)) "" else optString(key, "")

    private fun JSONObject.strOrNull(key: String): String? = str(key).takeIf { it.isNotEmpty() }

    private fun objs(a: JSONArray?): List<JSONObject> =
        if (a == null) emptyList() else (0 until a.length()).mapNotNull { a.optJSONObject(it) }

    fun who(o: JSONObject?): Who? = o?.let { val id = it.str("id"); if (id.isEmpty()) null else Who(id, it.str("name")) }

    fun item(o: JSONObject): ShoppingItem = ShoppingItem(
        id = o.str("id"),
        name = o.str("name"),
        quantity = o.str("quantity"),
        note = o.str("note"),
        category = o.str("category").ifEmpty { "other" },
        checked = o.optBoolean("checked", false),
        checkedAt = o.strOrNull("checked_at"),
        checkedBy = who(o.optJSONObject("checked_by")),
        addedBy = who(o.optJSONObject("added_by")),
        createdAt = o.strOrNull("created_at")
    )

    /** The screen payload; null when it has no `list` (an error body, an old server). */
    fun screen(o: JSONObject): ShoppingScreen? {
        val l = o.optJSONObject("list") ?: return null
        val list = ShoppingList(
            id = l.str("id"),
            name = l.str("name"),
            access = l.str("access").ifEmpty { "owner" },
            owner = who(l.optJSONObject("owner")),
            people = objs(l.optJSONArray("people")).map { ListPerson(it.str("id"), it.str("name"), it.str("access")) },
            version = l.optLong("version", 0)
        )
        if (list.id.isEmpty()) return null
        val items = objs(o.optJSONArray("items")).map(::item).filter { it.id.isNotEmpty() }
        val counts = o.optJSONObject("counts")
        return ShoppingScreen(
            list = list,
            categories = objs(o.optJSONArray("categories")).map { Aisle(it.str("key"), it.str("name")) },
            items = items,
            counts = Counts(
                counts?.optInt("to_buy", items.count { !it.checked }) ?: items.count { !it.checked },
                counts?.optInt("bought", items.count { it.checked }) ?: items.count { it.checked }
            ),
            usuals = objs(o.optJSONArray("usuals")).map { Usual(it.str("name"), it.str("category"), it.optInt("times")) },
            invites = objs(o.optJSONArray("invites")).map { Invite(it.str("list_id"), it.str("name"), who(it.optJSONObject("owner")), it.str("access")) },
            lists = objs(o.optJSONArray("lists")).map {
                ListRef(it.str("id"), it.str("name"), who(it.optJSONObject("owner")), it.str("access"), it.optBoolean("active"))
            }
        )
    }

    fun answer(o: JSONObject): WriteAnswer? {
        val screen = screen(o) ?: return null
        val undo = o.optJSONObject("undo")?.let { u -> u.str("id").takeIf { it.isNotEmpty() }?.let { UndoRef(it, u.str("label")) } }
        val results = objs(o.optJSONArray("results")).map { AddResult(it.str("id"), it.str("name"), it.str("status")) }
        return WriteAnswer(screen, undo, results)
    }

    fun usuals(o: JSONObject): List<Usual> =
        objs(o.optJSONArray("suggestions")).map { Usual(it.str("name"), it.str("category"), it.optInt("times")) }

    fun share(o: JSONObject): SharePayload = SharePayload(
        sharedWith = objs(o.optJSONArray("shared_with")).map { ListPerson(it.str("user_id"), it.str("name"), it.str("level")) },
        users = objs(o.optJSONArray("users")).mapNotNull { who(it) }
    )
}
