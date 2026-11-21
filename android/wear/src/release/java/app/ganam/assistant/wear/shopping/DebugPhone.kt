package app.ganam.assistant.wear.shopping

import android.content.Context

/** Release builds always talk to the real phone (the debug twin can stand in a fake one). */
object DebugPhone {
    fun link(context: Context): ShoppingLink? = null
}
