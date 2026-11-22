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
        assertEquals("K7QM4XPZ", PairingCode.normalize(" k7qm 4xpz "))
        assertEquals("K7QM4XPZ", PairingCode.normalize("k7qm-4xpz"))
        assertTrue(PairingCode.isComplete("K7QM4XPZ"))
        assertFalse(PairingCode.isComplete("K7QM4XP"))       // short
        assertFalse(PairingCode.isComplete("K7QM4XP0"))      // 0 isn't drawn
        assertFalse(PairingCode.isComplete("K7QM4XPI"))      // nor I
        assertFalse(PairingCode.isComplete("K7QM4XPZZ"))     // long
        assertEquals("K7QM 4XPZ", PairingCode.grouped("K7QM4XPZ"))
    }

    @Test fun pairAnswerNeedsAToken() {
        val answer = PairAnswer.decode(
            """{"token":"tok-1","device":{"id":"d1","name":"Pixel","kind":"phone","room":"","role":"","paired":true},"user":{"id":"u1","name":"Robin"},"server":{"version":"10.0.0"}}"""
        )
        assertEquals("tok-1", answer?.token)
        assertEquals(PairAnswerDevice("d1", "Pixel", "phone"), answer?.device)
        assertEquals(PairAnswerUser("u1", "Robin"), answer?.user)
        assertEquals("10.0.0", answer?.server?.version)
        assertNull(PairAnswer.decode("""{"device":{"id":"d1"}}"""))
        assertNull(PairAnswer.decode("not json"))
        assertNull(PairAnswer.decode(""))
    }

    @Test fun refusalsReadFromTheServersAnswer() {
        assertEquals(PairError.InvalidCode, PairError.from(403, """{"error":"invalid_code","detail":"x"}""", null))
        assertEquals(PairError.WrongKind("watch"), PairError.from(409, """{"error":"wrong_kind","expected":"watch"}""", null))
        assertEquals(PairError.RateLimited(90), PairError.from(429, """{"error":"rate_limited"}""", " 90 "))
        assertEquals("Too many wrong codes. Try again in 2 minutes.", PairError.RateLimited(90).message)
        assertEquals("Too many wrong codes. Try again in 30 seconds.", PairError.RateLimited(30).message)
        assertEquals(PairError.RateLimited(null), PairError.from(429, "", null))
        assertEquals(PairError.Invalid("kind must be one of phone"), PairError.from(400, """{"error":"invalid","detail":"kind must be one of phone"}""", null))
        assertEquals(PairError.Http(503), PairError.from(503, """{"error":"setup_required"}""", null))
        assertEquals(PairError.Http(502), PairError.from(502, "<html>bad gateway</html>", null))
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
