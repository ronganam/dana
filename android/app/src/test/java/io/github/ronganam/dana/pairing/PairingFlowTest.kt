package io.github.ronganam.dana.pairing

import io.github.ronganam.dana.data.discovery.DiscoveredServer
import io.github.ronganam.dana.data.pairing.PairError
import io.github.ronganam.dana.data.pairing.PairOutcome
import io.github.ronganam.dana.data.pairing.PairedDevice
import io.github.ronganam.dana.data.pairing.PairingFlow
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The tablet / TV pairing screen's state: the code, the address, what discovery fills, the call and its errors. */
class PairingFlowTest {
    private val device = PairedDevice("d1", "Hall TV", "tv", "Hall", "hall", "Robin")
    private val one = DiscoveredServer("Dana", "http://192.168.1.5:8080", "10.1.0")
    private val two = DiscoveredServer("Dana at the cabin", "http://192.168.7.2:8080", null)

    private class Recorder {
        val calls = mutableListOf<Pair<String, String>>()
        val stored = mutableListOf<Pair<String, PairOutcome.Paired>>()
        var answer: PairOutcome = PairOutcome.Failed(PairError.InvalidCode)
    }

    private fun TestScope.flow(rec: Recorder, initial: String = "") = PairingFlow(
        this, initial,
        pair = { s, c -> rec.calls += s to c; rec.answer },
        onPaired = { s, p -> rec.stored += s to p },
    )

    @Test fun codeKeepsOnlyCodeCharactersUpToEight() = runTest {
        val f = flow(Recorder())
        f.setCode("k7qm-4xpz99")
        assertEquals("K7QM4XPZ", f.state.value.code)
        f.setCode("io01ab")
        assertEquals("AB", f.state.value.code)
        f.type('c'.uppercaseChar()); f.backspace(); f.backspace()
        assertEquals("A", f.state.value.code)
    }

    @Test fun oneFoundServerFillsTheAddressSeveralWaitForAPick() = runTest {
        val f = flow(Recorder())
        f.discovered(listOf(one))
        assertEquals(one.url, f.state.value.server)
        f.discovered(listOf(one, two))
        assertEquals("still the one it had, as it is among them", one.url, f.state.value.server)
        f.discovered(listOf(two, DiscoveredServer("Other", "http://10.0.0.9:8080", null)))
        assertEquals("", f.state.value.server)
        f.pick(two)
        assertEquals(two.url, f.state.value.server)
        f.discovered(listOf(one))
        assertEquals("a picked address stays", two.url, f.state.value.server)
        assertEquals(listOf(one), f.state.value.found)
    }

    @Test fun typedOrSavedAddressIsNotReplacedByDiscovery() = runTest {
        val f = flow(Recorder(), initial = "https://dana.example.com")
        f.discovered(listOf(one))
        assertEquals("https://dana.example.com", f.state.value.server)
        val g = flow(Recorder())
        g.setServer("10.0.0.4:8080")
        g.discovered(listOf(one))
        assertEquals("10.0.0.4:8080", g.state.value.server)
    }

    @Test fun submitChecksBeforeCalling() = runTest {
        val rec = Recorder()
        val f = flow(rec)
        f.setCode("K7QM4XPZ")
        f.submit()
        assertTrue(f.state.value.error!!.contains("address"))
        f.setServer("http://192.168.1.5:8080")
        f.setCode("K7QM")
        assertNull("editing clears the error", f.state.value.error)
        assertFalse(f.state.value.canSubmit)
        f.submit()
        assertTrue(f.state.value.error!!.contains("8"))
        advanceUntilIdle()
        assertTrue(rec.calls.isEmpty())
    }

    @Test fun successStoresAndShowsTheDevice() = runTest {
        val rec = Recorder().apply { answer = PairOutcome.Paired("tok", device, "10.1.0") }
        val f = flow(rec)
        f.discovered(listOf(one))
        f.setCode("K7QM4XPZ")
        assertTrue(f.state.value.canSubmit)
        f.submit()
        assertTrue(f.state.value.busy)
        advanceUntilIdle()
        assertEquals(listOf(one.url to "K7QM4XPZ"), rec.calls)
        assertEquals(one.url, rec.stored.single().first)
        assertEquals("tok", rec.stored.single().second.token)
        assertEquals(device, f.state.value.paired)
        assertFalse(f.state.value.busy)
        assertEquals("", f.state.value.code)
    }

    @Test fun wrongCodeClearsItOtherErrorsKeepIt() = runTest {
        val rec = Recorder()
        val f = flow(rec, initial = "http://192.168.1.5:8080")
        f.setCode("K7QM4XPZ")
        f.submit(); advanceUntilIdle()
        assertEquals(PairError.InvalidCode.message, f.state.value.error)
        assertEquals("", f.state.value.code)
        assertNull(f.state.value.paired)

        rec.answer = PairOutcome.Failed(PairError.RateLimited(60))
        f.setCode("K7QM4XPZ")
        f.submit(); advanceUntilIdle()
        assertNotNull(f.state.value.error)
        assertEquals("K7QM4XPZ", f.state.value.code)
        assertTrue(rec.stored.isEmpty())
    }

    @Test fun aSecondSubmitWhileBusyIsIgnored() = runTest(StandardTestDispatcher()) {
        val gate = CompletableDeferred<PairOutcome>()
        var calls = 0
        val f = PairingFlow(this, "http://192.168.1.5:8080", pair = { _, _ -> calls++; gate.await() }, onPaired = { _, _ -> })
        f.setCode("K7QM4XPZ")
        f.submit(); f.submit()
        advanceUntilIdle()
        gate.complete(PairOutcome.Paired("tok", device, ""))
        advanceUntilIdle()
        assertEquals(1, calls)
    }
}
