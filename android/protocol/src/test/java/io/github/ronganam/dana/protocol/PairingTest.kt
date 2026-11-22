package io.github.ronganam.dana.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The QR link, code shapes, the pair answer and the watch's pairing messages. */
class PairingTest {
    @Test fun parsesTheDevicesPageLink() {
        val link = PairLink.parse("dana://pair?server=https%3A%2F%2Fdana.example.com&code=K7QM4XPZ")
        assertEquals(PairLink("https://dana.example.com", "K7QM4XPZ"), link)
    }

    @Test fun linkCodeIsNormalizedAndOrderDoesNotMatter() {
        val link = PairLink.parse(" DANA://PAIR?code=k7qm-4xpz&server=http://10.0.2.2:18081 ")
        assertEquals(PairLink("http://10.0.2.2:18081", "K7QM4XPZ"), link)
    }

    @Test fun otherLinksAreNotPairingLinks() {
        assertNull(PairLink.parse("https://dana.example.com/pair?server=x&code=K7QM4XPZ"))
        assertNull(PairLink.parse("dana://open?server=https%3A%2F%2Fx&code=K7QM4XPZ"))
        assertNull(PairLink.parse("dana://pair?server=https%3A%2F%2Fx"))
        assertNull(PairLink.parse("dana://pair?code=K7QM4XPZ"))
        assertNull(PairLink.parse("not a link at all"))
        assertNull(PairLink.parse(null))
    }

    @Test fun codeShape() {
        assertEquals("K7QM4XPZ", normalizePairCode("k7qm 4xpz"))
        assertTrue(isPairCodeShape("K7QM4XPZ"))
        assertFalse(isPairCodeShape("K7QM4XP"))       // short
        assertFalse(isPairCodeShape("K7QM4XP0"))      // 0 isn't drawn
        assertFalse(isPairCodeShape("K7QM4XPI"))      // nor I
        assertFalse(isPairCodeShape("K7QM4XPZZ"))     // long
    }

    @Test fun pairAnswerNeedsAToken() {
        val answer = PairAnswer.decode(
            """{"token":"tok-1","device":{"id":"d1","name":"Pixel","kind":"phone","paired":true},"user":{"id":"u1","name":"Robin"},"server":{"version":"10.0.0"}}"""
        )
        assertEquals("tok-1", answer?.token)
        assertEquals(PairDevice("d1", "Pixel", "phone"), answer?.device)
        assertEquals(PairUser("u1", "Robin"), answer?.user)
        assertNull(PairAnswer.decode("""{"device":{"id":"d1"}}"""))
        assertNull(PairAnswer.decode("not json"))
    }

    @Test fun failureMessages() {
        assertTrue(pairFailureMessage(403, "invalid_code").startsWith("That code isn't right"))
        assertTrue(pairFailureMessage(409, "wrong_kind").contains("another kind of device"))
        assertEquals("Too many wrong codes. Try again in 2 minutes.", pairFailureMessage(429, "rate_limited", 90))
        assertEquals("Too many wrong codes. Try again in 30 seconds.", pairFailureMessage(429, "rate_limited", 30))
        assertEquals("Too many wrong codes. Wait a few minutes and try again.", pairFailureMessage(429, "", null))
        assertTrue(pairFailureMessage(503, "setup_required").contains("isn't set up"))
        assertEquals("Couldn't pair (HTTP 500).", pairFailureMessage(500, ""))
    }

    @Test fun watchPairingMessagesRoundTrip() {
        assertEquals(WearPairRequest("Galaxy Watch"), WearPairRequest.decode(WearPairRequest("Galaxy Watch").encode()))
        val offer = WearPairOffer(server = "https://dana.example.com", code = "K7QM4XPZ")
        assertEquals(offer, WearPairOffer.decode(offer.encode()))
        assertEquals("""{"server":"https://dana.example.com","code":"K7QM4XPZ"}""", offer.encode())
        val refused = WearPairOffer.decode("""{"error":"Pair the phone with Dana first."}""")
        assertEquals("Pair the phone with Dana first.", refused?.error)
        assertEquals("", refused?.code)
        assertNull(WearPairOffer.decode("[]"))
    }
}
