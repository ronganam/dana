package io.github.ronganam.dana.tv

import android.content.Context
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.res.ResourcesCompat
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.RenderersFactory
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.exoplayer.mediacodec.MediaCodecUtil
import androidx.media3.exoplayer.video.MediaCodecVideoRenderer
import androidx.media3.exoplayer.rtsp.RtspMediaSource
import io.github.ronganam.dana.R
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import androidx.core.net.toUri

/**
 * Camera picture-in-picture over whatever is on screen.
 *
 * This TCL build has no system PiP (`android.software.picture_in_picture` is
 * missing), so it is a small overlay window: an accessibility overlay from
 * [DanaKeyService] when it runs (no extra permission), otherwise an app overlay
 * (needs "draw over other apps"). The window is neither focusable nor
 * touchable, so the remote keeps controlling the app underneath.
 *
 * Video is go2rtc's RTSP restream played by ExoPlayer over TCP with short
 * buffers (~0.5–1 s behind live). Video only, software decoded, on its own
 * thread: see [startPlayer].
 */
object CameraPip {
    private const val TAG = "CameraPip"
    private val main = Handler(Looper.getMainLooper())
    /** The player's own thread: building and releasing it must never block the UI. */
    private val pipHandler by lazy { Handler(HandlerThread("dana-pip").apply { start() }.looper) }

    private var window: View? = null
    private var windowManager: WindowManager? = null
    private var player: ExoPlayer? = null
    private var status: TextView? = null
    private var retries = 0
    private val _showing = MutableStateFlow<String?>(null)
    /** Id of the camera on screen, or null. */
    val showing: StateFlow<String?> = _showing.asStateFlow()

    fun toggle(context: Context, cameraId: String) = main.post {
        if (_showing.value == cameraId) hide() else show(context, cameraId)
    }

    fun show(context: Context, cameraId: String) {
        val camera = TvConfig.camera(cameraId) ?: run {
            Log.w(TAG, "No camera '$cameraId'")
            return
        }
        hide()
        val service = DanaKeyService.instance
        val (ctx, type) = when {
            service != null -> service to WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
            Settings.canDrawOverlays(context) -> context.applicationContext to WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else -> {
                Toast.makeText(context, "Enable the Dana TV button service to show cameras", Toast.LENGTH_LONG).show()
                return
            }
        }
        val wm = ctx.getSystemService(WindowManager::class.java)
        val settings = TvConfig.current
        val screenW = ctx.resources.displayMetrics.widthPixels
        val w = (screenW * settings.pipScale).toInt()
        val h = w * 9 / 16
        val dp = ctx.resources.displayMetrics.density

        val root = buildView(ctx, camera.name, dp)
        val params = WindowManager.LayoutParams(
            w, h, type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = when (settings.pipCorner) {
                Corner.TOP_LEFT -> Gravity.TOP or Gravity.START
                Corner.TOP_RIGHT -> Gravity.TOP or Gravity.END
                Corner.BOTTOM_LEFT -> Gravity.BOTTOM or Gravity.START
                Corner.BOTTOM_RIGHT -> Gravity.BOTTOM or Gravity.END
            }
            // TV safe area: 48 dp sides, 27 dp top/bottom.
            x = (48 * dp).toInt()
            y = (32 * dp).toInt()
            title = "Dana camera"
        }
        try {
            wm.addView(root, params)
        } catch (e: Exception) {
            Log.e(TAG, "Couldn't add the PiP window", e)
            return
        }
        window = root
        windowManager = wm
        _showing.value = cameraId
        retries = 0
        root.alpha = 0f
        root.animate().alpha(1f).setDuration(150).start()
        val surface: SurfaceView = root.findViewWithTag("surface")
        // The TV's router may hide LAN names (LanDns); resolve off the main thread, then play the address.
        Thread {
            val url = resolveHost(camera.url)
            pipHandler.post { if (_showing.value == cameraId && window === root) startPlayer(ctx, camera, url, surface) }
        }.start()
    }

    fun hide() {
        main.removeCallbacksAndMessages(null)
        pipHandler.removeCallbacksAndMessages(null)
        player?.let { p -> pipHandler.post { p.release() } }
        player = null
        window?.let { v -> runCatching { windowManager?.removeView(v) } }
        window = null
        windowManager = null
        status = null
        _showing.value = null
    }

    private fun buildView(ctx: Context, name: String, dp: Float): View {
        val mono = runCatching { ResourcesCompat.getFont(ctx, R.font.dm_mono_regular) }.getOrNull()
        val root = FrameLayout(ctx).apply {
            background = GradientDrawable().apply {
                setColor(0xFF13100E.toInt())          // DarkCanvas
                setStroke((1.5f * dp).toInt(), 0xFF766B5E.toInt()) // DarkOutline hairline
                cornerRadius = 10 * dp
            }
            val pad = (2 * dp).toInt()
            setPadding(pad, pad, pad, pad)
        }
        root.addView(SurfaceView(ctx).apply { tag = "surface" }, FrameLayout.LayoutParams(-1, -1))
        status = TextView(ctx).apply {
            text = "CONNECTING…"
            typeface = mono
            setTextColor(0xFFCBBFAD.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            letterSpacing = 0.08f
        }
        root.addView(status, FrameLayout.LayoutParams(-2, -2, Gravity.CENTER))
        root.addView(TextView(ctx).apply {
            text = "●  " + name.uppercase()
            typeface = mono
            setTextColor(0xFFF2EBDD.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            letterSpacing = 0.08f
            val p = (6 * dp).toInt()
            setPadding(p + p / 2, p / 2, p + p / 2, p / 2)
            background = GradientDrawable().apply {
                setColor(0xB313100E.toInt())
                cornerRadius = 6 * dp
            }
        }, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.START).apply {
            val m = (8 * dp).toInt(); setMargins(m, m, m, m)
        })
        return root
    }

    private fun resolveHost(url: String): String = runCatching {
        val uri = url.toUri()
        val host = uri.host ?: return url
        val ip = LanDns.resolveToIp(host)
        if (ip == host) url else url.replaceFirst("//$host", "//$ip")
    }.getOrDefault(url)

    /**
     * Runs on [pipHandler]'s thread, which the player then lives on.
     *
     * - Video only: no audio renderer at all. Creating ExoPlayer's default audio
     *   sink queries the audio policy service, and when this TV's audioserver is
     *   wedged (it happens; see AGENTS.md) that call blocks for good — on the main
     *   thread it was an ANR and the PiP stuck at "Connecting…".
     * - Software decoding: this TV's video hardware can't run a second decoder
     *   next to the app's (opening one tears down e.g. YouTube's 4K AV1 decoder,
     *   and its playback stalls). The camera substreams are ≤ 704×576.
     */
    private fun startPlayer(ctx: Context, camera: Camera, url: String, surface: SurfaceView) {
        val loadControl = DefaultLoadControl.Builder()
            .setBufferDurationsMs(500, 2000, 100, 100)
            .setPrioritizeTimeOverSizeThresholds(true)
            .build()
        val softwareFirst = MediaCodecSelector { mime, secure, tunneling ->
            val all = MediaCodecUtil.getDecoderInfos(mime, secure, tunneling)
            all.filter { it.softwareOnly }.ifEmpty { all }
        }
        val videoOnly = RenderersFactory { handler, videoListener, _, _, _ ->
            arrayOf(
                MediaCodecVideoRenderer.Builder(ctx)
                    .setMediaCodecSelector(softwareFirst)
                    .setAllowedJoiningTimeMs(0L)
                    .setEventHandler(handler)
                    .setEventListener(videoListener)
                    .setMaxDroppedFramesToNotify(50)
                    .build()
            )
        }
        val p = ExoPlayer.Builder(ctx, videoOnly)
            .setLooper(pipHandler.looper)
            .setLoadControl(loadControl)
            .build()
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, true)
            .build()
        // The surface's callbacks come on the main thread; the player is only touched on its own.
        main.post {
            val holder = surface.holder
            if (holder.surface?.isValid == true) pipHandler.post { p.setVideoSurface(holder.surface) }
            holder.addCallback(object : SurfaceHolder.Callback {
                override fun surfaceCreated(h: SurfaceHolder) { pipHandler.post { p.setVideoSurface(h.surface) } }
                override fun surfaceChanged(h: SurfaceHolder, format: Int, width: Int, height: Int) {}
                override fun surfaceDestroyed(h: SurfaceHolder) {
                    // Wait for the player to let go before the surface goes away.
                    val done = java.util.concurrent.CountDownLatch(1)
                    pipHandler.post { runCatching { p.clearVideoSurface() }; done.countDown() }
                    done.await(500, java.util.concurrent.TimeUnit.MILLISECONDS)
                }
            })
        }
        p.addListener(object : Player.Listener {
            override fun onRenderedFirstFrame() {
                main.post { retries = 0; status?.visibility = View.GONE }
            }

            override fun onPlayerError(error: PlaybackException) {
                Log.w(TAG, "${camera.id}: ${error.errorCodeName}", error)
                main.post {
                    status?.apply { visibility = View.VISIBLE; text = "CAMERA UNAVAILABLE · RETRYING" }
                    val delay = (2000L shl retries.coerceAtMost(3)).coerceAtMost(15_000L)
                    retries++
                    pipHandler.postDelayed({
                        if (_showing.value == camera.id && player === p) { p.prepare(); p.play() }
                    }, delay)
                }
            }
        })
        p.setMediaSource(
            RtspMediaSource.Factory()
                .setForceUseRtpTcp(true)
                .setTimeoutMs(8_000)
                .createMediaSource(MediaItem.fromUri(url.toUri()))
        )
        p.playWhenReady = true
        p.prepare()
        main.post {
            // Hidden (or another camera shown) while the player was being built.
            if (_showing.value == camera.id && window != null && player == null) player = p
            else pipHandler.post { p.release() }
        }
    }
}
