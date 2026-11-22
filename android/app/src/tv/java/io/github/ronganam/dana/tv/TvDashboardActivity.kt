package io.github.ronganam.dana.tv

import android.annotation.SuppressLint
import android.graphics.Color
import android.net.http.SslError
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.network.WebSignIn
import io.github.ronganam.dana.data.pairing.DevicePairing
import kotlinx.coroutines.launch

/**
 * The TV's own device dashboard (server `docs/dashboard.md`: the preset and
 * widgets chosen on the web, Devices → the TV; Living room unless its role says
 * otherwise) full screen in a WebView, opened from the home screen's Dashboard
 * row. It signs in like the kiosk: the device token at `POST /login` for the
 * session cookie. The remote's D-pad moves between the page's focusable
 * controls (or scrolls when there are none); Back returns to the home screen.
 */
class TvDashboardActivity : ComponentActivity() {
    private val main = Handler(Looper.getMainLooper())
    private var webView: WebView? = null
    /** Whether the last load signed in with the token (then a login page is not met by signing in again). */
    private var lastLoadSignedIn = false
    private var reloadPending = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        hideSystemBars()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = finish()
        })
        if (!ServerConfig.load(this).isConfigured) {
            finish()
            return
        }
        showWebView()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    override fun onStop() {
        CookieManager.getInstance().flush()
        super.onStop()
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        webView?.let { webView = null; it.destroy() }
        super.onDestroy()
    }

    private fun hideSystemBars() {
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun showWebView() {
        val view = buildWebView()
        webView = view
        setContentView(view)
        view.requestFocus()
        loadWhenSized(view)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun buildWebView(): WebView = WebView(this).apply {
        layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        setBackgroundColor(Color.BLACK)
        isFocusable = true
        isFocusableInTouchMode = true
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        settings.userAgentString = settings.userAgentString + " DanaTV/" + BuildConfig.VERSION_NAME
        CookieManager.getInstance().setAcceptCookie(true)
        webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = false

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    Log.w(TAG, "Dashboard failed to load: ${error.errorCode} ${error.description}")
                    scheduleReload(view)
                }
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (request.isForMainFrame && response.statusCode >= 500) {
                    Log.w(TAG, "Dashboard HTTP ${response.statusCode}")
                    scheduleReload(view)
                }
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                Log.w(TAG, "SSL error on ${error.url}: ${error.primaryError}")
                handler.cancel()
                scheduleReload(view)
            }

            override fun onPageFinished(view: WebView, url: String) {
                when (WebSignIn.landing(ServerConfig.load(this@TvDashboardActivity).url, url)) {
                    WebSignIn.LoginLanding.NONE -> {}
                    // The cookie went stale: sign in with the token again (once; then retry later).
                    WebSignIn.LoginLanding.SIGN_IN_AGAIN -> if (lastLoadSignedIn) scheduleReload(view) else {
                        CookieManager.getInstance().removeAllCookies { if (webView === view) loadDashboard(view) }
                    }
                    // `/login` refused the token: the TV was removed on the Devices page (or its token revoked).
                    WebSignIn.LoginLanding.REFUSED -> unpaired()
                }
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                Log.e(TAG, "WebView renderer gone (crash=${detail.didCrash()}); rebuilding")
                if (webView === view) webView = null
                (view.parent as? ViewGroup)?.removeView(view)
                view.destroy()
                showWebView()
                return true
            }
        }
    }

    /** Navigate once the WebView has its real size (a first load at 0×0 leaves `100dvh` at 0, as on the kiosk). */
    private fun loadWhenSized(view: WebView) {
        if (view.width > 0 && view.height > 0) {
            loadDashboard(view)
            return
        }
        view.addOnLayoutChangeListener(object : View.OnLayoutChangeListener {
            override fun onLayoutChange(v: View, l: Int, t: Int, r: Int, b: Int, ol: Int, ot: Int, or: Int, ob: Int) {
                if (r - l > 0 && b - t > 0) {
                    v.removeOnLayoutChangeListener(this)
                    v.post { if (webView === view) loadDashboard(view) }
                }
            }
        })
    }

    private fun scheduleReload(view: WebView) {
        if (reloadPending) return
        reloadPending = true
        main.postDelayed({
            reloadPending = false
            if (webView === view) loadDashboard(view)
        }, 15_000)
    }

    private fun loadDashboard(view: WebView) {
        val (server, token) = ServerConfig.load(this)
        val load = WebSignIn.load(server, token, PATH, CookieManager.getInstance().getCookie(server))
        lastLoadSignedIn = load is WebSignIn.Load.Post
        when (load) {
            is WebSignIn.Load.Post -> view.postUrl(load.url, load.body.toByteArray())
            is WebSignIn.Load.Get -> view.loadUrl(load.url)
        }
    }

    /** The server no longer takes this TV's token: forget the pairing; the home screen then shows the pairing screen. */
    private fun unpaired() {
        Log.w(TAG, "The server refused this TV's token; it is no longer paired")
        lifecycleScope.launch {
            DevicePairing.unpair(this@TvDashboardActivity)
            CookieManager.getInstance().removeAllCookies(null)
            finish()
        }
    }

    companion object {
        private const val TAG = "TvDashboard"

        /** The dashboard page; a TV has its own brightness, so the page's scheduled night dimming is off. */
        const val PATH = "/dashboard?night=off"
    }
}
