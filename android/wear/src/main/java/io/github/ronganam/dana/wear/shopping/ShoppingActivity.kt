package io.github.ronganam.dana.wear.shopping

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.speech.RecognizerIntent
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import io.github.ronganam.dana.wear.ui.theme.DanaWearTheme

/**
 * The Shopping screen: opened from the Shopping complication and tile, the
 * side menu and its own launcher entry ("Shopping"). State lives in
 * [WatchShopping], so the tile, the complication and the screen agree.
 */
class ShoppingActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WatchShopping.init(this)
        val speech = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "What to add?")
        val canSpeak = speech.resolveActivity(packageManager) != null

        setContent {
            DanaWearTheme {
                val state by WatchShopping.state.collectAsState()
                val listen = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
                    val text = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
                    if (result.resultCode == Activity.RESULT_OK && !text.isNullOrBlank()) WatchShopping.add(this, text)
                }
                val now = remember(state) { WatchShopping.now() }
                ShoppingScreen(
                    state = state,
                    now = now,
                    onTick = WatchShopping::tick,
                    onUntick = WatchShopping::untick,
                    onAdd = if (canSpeak) ({ runCatching { listen.launch(speech) } }) else null
                )
            }
        }
    }

    override fun onStart() {
        super.onStart()
        WatchShopping.startVisit(this)
    }

    override fun onStop() {
        super.onStop()
        WatchShopping.endVisit()
    }
}
