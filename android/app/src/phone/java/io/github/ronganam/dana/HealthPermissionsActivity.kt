package io.github.ronganam.dana

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.DanaTheme

/**
 * Why Dana asks Health Connect for sleep: Health Connect opens this from its
 * permission screen ("privacy policy", Android 13 and older:
 * `ACTION_SHOW_PERMISSIONS_RATIONALE`; Android 14+: the
 * `VIEW_PERMISSION_USAGE` alias in the phone manifest). Without it Health
 * Connect refuses to show the permission screen.
 */
class HealthPermissionsActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            DanaTheme {
                Surface(Modifier.fillMaxSize()) {
                    Column(
                        Modifier.systemBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Text(getString(R.string.health_rationale_title), style = MaterialTheme.typography.headlineSmall)
                        Text(getString(R.string.health_rationale_body), style = MaterialTheme.typography.bodyLarge)
                        TextButton(onClick = { finish() }) { Text(getString(R.string.health_rationale_close)) }
                    }
                }
            }
        }
    }
}
