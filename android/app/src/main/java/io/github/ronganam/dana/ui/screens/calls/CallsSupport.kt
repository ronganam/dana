package io.github.ronganam.dana.ui.screens.calls

import androidx.compose.material.icons.filled.Phone
import io.github.ronganam.dana.ui.components.ScreenTopBar
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.theme.Info
import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import android.util.Log
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.PhoneInTalk
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import io.github.ronganam.dana.data.models.PhoneCall
import io.github.ronganam.dana.ui.components.GlassCard
import io.github.ronganam.dana.ui.theme.isWideLayout
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.PendingRecordingFile
import io.github.ronganam.dana.ui.viewmodels.UploadFormState
import androidx.core.net.toUri

@Composable
fun RtlSupportingText(
    text: AnnotatedString,
    color: Color,
    style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.bodyMedium.copy(
        lineHeight = 20.sp,
        fontSize = 14.sp
    ),
    maxLines: Int = Int.MAX_VALUE
) {
    val isRtl = remember(text) { isHebrew(text.text) }
    val direction = if (isRtl) LayoutDirection.Rtl else LayoutDirection.Ltr
    val alignment = if (isRtl) TextAlign.Right else TextAlign.Left

    CompositionLocalProvider(LocalLayoutDirection provides direction) {
        Text(
            text = text,
            color = color,
            textAlign = alignment,
            style = style,
            maxLines = maxLines,
            overflow = if (maxLines == Int.MAX_VALUE) TextOverflow.Clip else TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth()
        )
    }
}

@Composable
fun RtlSupportingText(
    text: String,
    color: Color,
    style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.bodyMedium.copy(
        lineHeight = 20.sp,
        fontSize = 14.sp
    ),
    maxLines: Int = Int.MAX_VALUE
) {
    RtlSupportingText(
        text = AnnotatedString(text),
        color = color,
        style = style,
        maxLines = maxLines
    )
}

// Helper utilities
fun isHebrew(text: String): Boolean {
    return text.any { it in '\u0590'..'\u05FF' }
}

fun parseMarkdownToAnnotatedString(text: String): AnnotatedString {
    return buildAnnotatedString {
        val parts = text.split("**")
        for (i in parts.indices) {
            val part = parts[i]
            if (i % 2 == 1) {
                pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                append(part)
                pop()
            } else {
                append(part)
            }
        }
    }
}

fun formatCallTimestamp(isoString: String): String {
    return try {
        val parts = isoString.split("T")
        if (parts.size < 2) return isoString
        val dateParts = parts[0].split("-")
        val timeParts = parts[1].split(":")
        if (dateParts.size < 3 || timeParts.size < 2) return isoString
        
        val year = dateParts[0]
        val monthNum = dateParts[1].toInt()
        val day = dateParts[2].toInt()
        
        val hour = timeParts[0]
        val minute = timeParts[1]
        
        val monthName = when (monthNum) {
            1 -> "Jan"
            2 -> "Feb"
            3 -> "Mar"
            4 -> "Apr"
            5 -> "May"
            6 -> "Jun"
            7 -> "Jul"
            8 -> "Aug"
            9 -> "Sep"
            10 -> "Oct"
            11 -> "Nov"
            12 -> "Dec"
            else -> ""
        }
        
        "$monthName $day, $year at $hour:$minute"
    } catch (e: Exception) {
        isoString
    }
}

fun formatMsec(msec: Int): String {
    val totalSec = msec / 1000
    val min = totalSec / 60
    val sec = totalSec % 60
    return String.format(java.util.Locale.getDefault(), "%d:%02d", min, sec)
}

fun getDisplayName(context: Context, uri: Uri): String {
    return try {
        val cursor = context.contentResolver.query(uri, null, null, null, null)
        cursor?.use {
            val nameIdx = it.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (nameIdx >= 0 && it.moveToFirst()) it.getString(nameIdx) ?: "audio"
            else "audio"
        } ?: "audio"
    } catch (e: Exception) {
        "audio"
    }
}


fun formatDuration(totalSeconds: Int): String {
    val min = totalSeconds / 60
    val sec = totalSeconds % 60
    return String.format(java.util.Locale.getDefault(), "%d:%02d", min, sec)
}

class CallAudioPlayer(
    private val context: Context,
    private val serverUrl: String,
    private val apiToken: String
) {
    private var mediaPlayer: MediaPlayer? = null
    val currentPlayingCallId: MutableState<String?> = mutableStateOf(null)
    val isPlaying: MutableState<Boolean> = mutableStateOf(false)
    val currentPosition: MutableFloatState = mutableFloatStateOf(0f)
    val duration: MutableIntState = mutableIntStateOf(0)

    private val handler = Handler(Looper.getMainLooper())
    private val updateProgressAction = object : Runnable {
        override fun run() {
            mediaPlayer?.let {
                if (it.isPlaying) {
                    this@CallAudioPlayer.currentPosition.floatValue = it.currentPosition.toFloat()
                    handler.postDelayed(this, 250)
                }
            }
        }
    }

    fun togglePlay(callId: String) {
        if (currentPlayingCallId.value == callId) {
            if (isPlaying.value) {
                pause()
            } else {
                mediaPlayer?.start()
                isPlaying.value = true
                handler.post(updateProgressAction)
            }
            return
        }

        stop()

        val cleanUrl = serverUrl.removeSuffix("/")
        val url = "$cleanUrl/api/calls/$callId/audio"
        val uri = url.toUri()
        val headers = HashMap<String, String>()
        if (apiToken.isNotEmpty()) {
            headers["Authorization"] = "Bearer $apiToken"
        }

        mediaPlayer = MediaPlayer().apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .build()
            )
            try {
                setDataSource(context, uri, headers)
                prepareAsync()
                setOnPreparedListener {
                    start()
                    this@CallAudioPlayer.currentPlayingCallId.value = callId
                    this@CallAudioPlayer.isPlaying.value = true
                    this@CallAudioPlayer.duration.intValue = it.duration
                    this@CallAudioPlayer.currentPosition.floatValue = 0f
                    handler.post(updateProgressAction)
                }
                setOnCompletionListener {
                    this@CallAudioPlayer.isPlaying.value = false
                    this@CallAudioPlayer.currentPosition.floatValue = 0f
                    handler.removeCallbacks(updateProgressAction)
                }
            } catch (e: Exception) {
                Log.e("CallAudioPlayer", "Error starting playback", e)
            }
        }
    }

    fun pause() {
        mediaPlayer?.pause()
        isPlaying.value = false
        handler.removeCallbacks(updateProgressAction)
    }

    fun seekTo(msec: Float) {
        mediaPlayer?.seekTo(msec.toInt())
        currentPosition.floatValue = msec
    }

    fun stop() {
        mediaPlayer?.stop()
        mediaPlayer?.release()
        mediaPlayer = null
        currentPlayingCallId.value = null
        isPlaying.value = false
        currentPosition.floatValue = 0f
        duration.intValue = 0
        handler.removeCallbacks(updateProgressAction)
    }
}

