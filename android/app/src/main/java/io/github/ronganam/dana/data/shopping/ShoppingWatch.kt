package io.github.ronganam.dana.data.shopping

import android.content.Context
import kotlinx.coroutines.CancellationException
import org.json.JSONObject

/**
 * What the watch asks of the shopping list (`/shopping_action`, handled in
 * `WearMessageListenerService`). It goes through [ShoppingSync] like a tap on
 * the phone, so it is shown at once, works offline (queued) and the next
 * snapshot the watch gets already has it:
 *
 *   {action: "check" | "uncheck", id, list_id, ref}   tick / untick an item
 *   {action: "add", text, list_id, ref}               add typed text (split like the phone's add field)
 *
 * Answer (`/response/shopping_action`): `{ok, queued, error?, ref}`; `error`
 * is a sentence to show (read-only list, item gone …), `ref` is the watch's
 * own id for the request, sent back as it came.
 */
object ShoppingWatch {
    suspend fun handle(context: Context, json: JSONObject): JSONObject =
        answer(ShoppingSync.get(context), json) { ShoppingSync.submit(context, it) }

    /** The answer to one watch request on [store]; [submit] makes the change (the process's store in the app). */
    suspend fun answer(
        store: ShoppingStore,
        json: JSONObject,
        submit: suspend (ShoppingOp) -> ShoppingStore.Outcome = { store.submit(it) }
    ): JSONObject {
        val ref = json.optString("ref")
        val out = try {
            outcome(store, json, submit)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            refused("Couldn't do that on the phone.")
        }
        return if (ref.isEmpty()) out else out.put("ref", ref)
    }

    private suspend fun outcome(store: ShoppingStore, json: JSONObject, submit: suspend (ShoppingOp) -> ShoppingStore.Outcome): JSONObject {
        store.load()
        val shown = store.shown()
        val listId = json.optString("list_id").ifEmpty { shown?.list?.id.orEmpty() }
        if (listId.isEmpty()) return refused("The list hasn't loaded yet.")
        val id = json.optString("id")
        val name = shown?.items?.firstOrNull { it.id == id }?.name.orEmpty()
        val op: ShoppingOp = when (json.optString("action")) {
            "check" -> ShoppingOp.Check(ShoppingOp.newId(), listId, id, true, name)
            "uncheck" -> ShoppingOp.Check(ShoppingOp.newId(), listId, id, false, name)
            "add" -> ShoppingOps.addTyped(listId, json.optString("text")) ?: return refused("Nothing to add.")
            else -> return refused("Unknown action.")
        }
        return when (val outcome = submit(op)) {
            is ShoppingStore.Outcome.Sent -> JSONObject().put("ok", true).put("queued", false)
            ShoppingStore.Outcome.Queued -> JSONObject().put("ok", true).put("queued", true)
            is ShoppingStore.Outcome.Rejected -> refused(ShoppingView.errorText(outcome.status, outcome.body))
        }
    }

    private fun refused(sentence: String): JSONObject = JSONObject().put("ok", false).put("error", sentence)
}
