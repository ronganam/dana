package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.network.WebSignIn.LoginLanding
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** How the kiosk's and the TV's dashboard WebViews sign in and read a login page. */
class WebSignInTest {
    private val server = "https://dana.example.com"

    @Test fun signsInWithTheTokenWithoutASessionCookie() {
        val load = WebSignIn.load(server, "tok en/+", "/dashboard?zoom=98", cookies = "other=1")
        assertEquals(
            WebSignIn.Load.Post("https://dana.example.com/login", "token=tok+en%2F%2B&next=%2Fdashboard%3Fzoom%3D98"),
            load
        )
    }

    @Test fun loadsThePageWithASessionCookie() {
        val load = WebSignIn.load(server, "token", "/dashboard?night=off", cookies = "a=b; dana_session=xyz")
        assertEquals(WebSignIn.Load.Get("https://dana.example.com/dashboard?night=off"), load)
    }

    @Test fun noTokenJustLoads() {
        assertEquals(WebSignIn.Load.Get("$server/dashboard"), WebSignIn.load(server, "", "/dashboard", null))
    }

    @Test fun sessionCookieIsMatchedByName() {
        assertTrue(WebSignIn.hasSession("dana_session=1"))
        assertFalse(WebSignIn.hasSession("old_dana_session=1"))
        assertFalse(WebSignIn.hasSession(null))
    }

    @Test fun loginLandings() {
        assertEquals(LoginLanding.NONE, WebSignIn.landing(server, "$server/dashboard?night=off"))
        assertEquals(LoginLanding.NONE, WebSignIn.landing(server, "$server/login-help"))
        assertEquals(LoginLanding.NONE, WebSignIn.landing(server, "https://elsewhere.example.com/login?error=1"))
        assertEquals(LoginLanding.SIGN_IN_AGAIN, WebSignIn.landing(server, "$server/login?next=%2Fdashboard"))
        assertEquals(LoginLanding.REFUSED, WebSignIn.landing(server, "$server/login?error=1&next=%2Fdashboard"))
        assertEquals(LoginLanding.REFUSED, WebSignIn.landing(server, "$server/login?next=%2F&error=limited"))
    }
}
