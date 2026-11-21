package app.ganam.assistant

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewmodel.compose.viewModel
import app.ganam.assistant.ui.theme.GanamAssistantTheme
import app.ganam.assistant.ui.viewmodels.MainViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Intent extra: open the Inbox tab (Dana's snooze / alert notifications). */
const val EXTRA_OPEN_INBOX = "app.ganam.assistant.OPEN_INBOX"

class MainActivity : ComponentActivity() {
    private companion object {
        const val EXTRA_SHARE_HANDLED = "app.ganam.assistant.SHARE_HANDLED"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        if (packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_WATCH)) {
            setContent {
                MaterialTheme {
                    Box(
                        modifier = Modifier
                            .fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "This is the Dana phone app.\nPlease install it on your phone.",
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(24.dp)
                        )
                    }
                }
            }
            return
        }
        
        val permissionsToRequest = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissionsToRequest.add(android.Manifest.permission.POST_NOTIFICATIONS)
            permissionsToRequest.add(android.Manifest.permission.READ_MEDIA_AUDIO)
        } else {
            permissionsToRequest.add(android.Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        // Call direction / number / name for auto-uploaded recordings.
        permissionsToRequest.add(android.Manifest.permission.READ_CALL_LOG)
        // Caller card: notice when the phone rings (phone build only declares it).
        if (!BuildConfig.KIOSK && !BuildConfig.TV) permissionsToRequest.add(android.Manifest.permission.READ_PHONE_STATE)

        val ungranted = permissionsToRequest.filter {
            checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED
        }

        if (ungranted.isNotEmpty()) {
            requestPermissions(ungranted.toTypedArray(), 1001)
        } else {
            startCallObserverService()
        }

        app.ganam.assistant.ui.theme.ThemeSettings.load(applicationContext)
        setContent {
            GanamAssistantTheme {
                val mainViewModel: MainViewModel = viewModel()
                LaunchedEffect(Unit) {
                    mainViewModel.initialize(applicationContext)
                }
                // The main screen: Now / Reminders / Notifications / More + ask bar + thread.
                app.ganam.assistant.ui.shell.DanaShell(mainViewModel)
            }
        }

        DanaWidgets.schedule(applicationContext)

        if (savedInstanceState == null) {
            handleShareIntent(intent)
            handleDeepLink(intent)
        }
    }

    /** From the Debrief / notifications: open a chat with something attached, or a call. */
    private fun handleDeepLink(intent: Intent?) {
        intent ?: return
        val vm = ViewModelProvider(this)[MainViewModel::class.java]
        val kind = intent.getStringExtra(CallDebriefActivity.EXTRA_CONTEXT_KIND)
        val id = intent.getStringExtra(CallDebriefActivity.EXTRA_CONTEXT_ID)
        val callId = intent.getStringExtra(CallDebriefActivity.EXTRA_OPEN_CALL_ID)
        val openInbox = intent.getBooleanExtra(EXTRA_OPEN_INBOX, false)
        val personId = intent.getStringExtra(EXTRA_OPEN_PERSON_ID)
        val openReview = intent.getBooleanExtra(EXTRA_OPEN_REVIEW, false)
        val openScreen = intent.getStringExtra(EXTRA_OPEN_SCREEN)
        when {
            kind != null && id != null -> vm.openContextChat(kind, id, intent.getStringExtra(CallDebriefActivity.EXTRA_CONTEXT_TITLE).orEmpty())
            callId != null -> vm.openCall(callId)
            personId != null -> vm.openPerson(personId)
            openReview -> vm.openReview()
            openInbox -> vm.navigateTo("inbox")
            // "waiting" (the Waiting-on-you widget) opens Now.
            openScreen == "waiting" -> vm.navigateTo("now")
            openScreen != null -> vm.navigateTo(openScreen)
            else -> return
        }
        intent.removeExtra(EXTRA_OPEN_INBOX)
        intent.removeExtra(EXTRA_OPEN_PERSON_ID)
        intent.removeExtra(EXTRA_OPEN_REVIEW)
        intent.removeExtra(EXTRA_OPEN_SCREEN)
        // Handle once (not again after a configuration change).
        intent.removeExtra(CallDebriefActivity.EXTRA_CONTEXT_KIND)
        intent.removeExtra(CallDebriefActivity.EXTRA_CONTEXT_ID)
        intent.removeExtra(CallDebriefActivity.EXTRA_CONTEXT_TITLE)
        intent.removeExtra(CallDebriefActivity.EXTRA_OPEN_CALL_ID)
    }

    override fun onStart() {
        super.onStart()
        // Poll server reachability only while the app is visible.
        app.ganam.assistant.data.network.ServerStatusMonitor.start(this)
        if (!packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_WATCH)) {
            ViewModelProvider(this)[MainViewModel::class.java].onAppForeground()
        }
    }

    override fun onStop() {
        super.onStop()
        app.ganam.assistant.data.network.ServerStatusMonitor.stop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleShareIntent(intent)
        handleDeepLink(intent)
    }

    private fun handleShareIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        // Handle each share once (not again after a configuration change).
        if (intent.getBooleanExtra(EXTRA_SHARE_HANDLED, false)) return
        intent.putExtra(EXTRA_SHARE_HANDLED, true)

        val uri = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
        }
        val type = intent.type.orEmpty()
        val vm = ViewModelProvider(this)[MainViewModel::class.java]
        try {
            when {
                type.startsWith("audio/") && uri != null -> {
                    vm.setSharedAudioUri(uri)
                    vm.navigateTo("calls")
                }
                type.startsWith("image/") && uri != null -> {
                    val text = sharedText(intent)
                    // The read grant on a shared content:// URI ends with this
                    // activity, so keep our own copy for the upload.
                    lifecycleScope.launch {
                        val copy = withContext(Dispatchers.IO) { copySharedImage(uri, contentResolver.getType(uri)) }
                        vm.startChatFromShare(text, copy ?: uri)
                    }
                }
                else -> {
                    val text = sharedText(intent) ?: return
                    vm.startChatFromShare(text, null)
                }
            }
        } catch (e: Exception) {
            Log.e("MainActivity", "Error handling share intent", e)
        }
    }

    /** Subject and text of a share, e.g. an article's title and link. */
    private fun sharedText(intent: Intent): String? {
        val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim().orEmpty()
        val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.trim().orEmpty()
        val combined = when {
            subject.isNotEmpty() && text.isNotEmpty() && !text.contains(subject) -> "$subject\n$text"
            text.isNotEmpty() -> text
            else -> subject
        }
        return combined.ifEmpty { null }
    }

    private fun copySharedImage(uri: Uri, mimeType: String?): Uri? = try {
        val dir = java.io.File(cacheDir, "shared").apply { mkdirs() }
        // Keep the folder small: drop earlier shares.
        dir.listFiles()?.forEach { it.delete() }
        val ext = android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(mimeType ?: "") ?: "jpg"
        val out = java.io.File(dir, "share_${System.currentTimeMillis()}.$ext")
        contentResolver.openInputStream(uri)?.use { input -> out.outputStream().use { input.copyTo(it) } }
            ?: throw java.io.IOException("Couldn't open the shared image")
        Uri.fromFile(out)
    } catch (e: Exception) {
        Log.e("MainActivity", "Couldn't copy the shared image", e)
        null
    }

    private fun startCallObserverService() {
        CallRecordingObserverService.start(this)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1001) {
            val hasStoragePermission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                checkSelfPermission(android.Manifest.permission.READ_MEDIA_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED
            } else {
                checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) == android.content.pm.PackageManager.PERMISSION_GRANTED
            }
            if (hasStoragePermission) {
                startCallObserverService()
            }
        }
    }
}
