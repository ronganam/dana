package io.github.ronganam.dana.about

import io.github.ronganam.dana.data.network.AboutPageWire
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.ChecksWire
import io.github.ronganam.dana.data.network.ShortcutsWire
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AboutApiTest {
    private fun page(text: String) = ApiJson.decodeFromString(AboutPageWire.serializer(), text).toModel()
    private fun shortcuts(text: String) = ApiJson.decodeFromString(ShortcutsWire.serializer(), text).toModel()
    private fun checks(text: String) = ApiJson.decodeFromString(ChecksWire.serializer(), text).toModel()

    @Test
    fun parsesSectionsAndFacts() {
        val page = page(
            (
                """
                {"sections": [
                  {"key": "people", "label": "People", "facts": [
                    {"id": "f1", "text": "Mom's birthday is 3 March", "source": "a call with אמא", "source_kind": "call"},
                    {"id": "f2", "text": "Gal is my brother", "source_kind": "told"},
                    {"text": "no id"}
                  ]},
                  {"key": "home", "facts": []},
                  {"key": "other", "label": null, "facts": [{"id": "f3", "text": "Likes tea", "source": "your old Memory list", "source_kind": "imported"}]}
                ], "used_chars": 120, "max_chars": 4000}
                """
            )
        )
        assertEquals(listOf("people", "home", "other"), page.sections.map { it.key })
        assertEquals("Home", page.sections[1].label)
        assertEquals("Other", page.sections[2].label)
        assertEquals(3, page.factCount)
        val f1 = page.sections[0].facts[0]
        assertEquals("learned from a call with אמא", f1.sourceLine)
        assertEquals("people", f1.section)
        assertEquals("you told Dana", page.sections[0].facts[1].sourceLine)
        assertEquals("from your old Memory list", page.sections[2].facts[0].sourceLine)
        assertEquals(120, page.usedChars)
        assertEquals(4000, page.maxChars)
    }

    @Test
    fun learnedPhrasesComeFirstThenByUse() {
        val list = shortcuts(
            (
                """
                {"enabled": true, "learn_after": 3, "shortcuts": [
                  {"id": "s1", "phrase": "open the gate", "does": "Open cover Gate", "active": true, "times_used": 2},
                  {"id": "s2", "phrase": "lights off", "tool": "call_ha_service", "active": false, "seen": 1},
                  {"id": "s3", "phrase": "תדליקי אור בסלון", "does": "Turn on Living room", "active": true, "times_used": 9}
                ]}
                """
            )
        )
        assertEquals(listOf("s3", "s1", "s2"), list.map { it.id })
        assertEquals("call_ha_service", list[2].does)
        assertEquals(3, list[2].learnAfter)
        assertFalse(list[2].active)
    }

    @Test
    fun failedChecksComeFirst() {
        val checks = checks(
            (
                """
                {"checks": [
                  {"key": "ha", "label": "Home Assistant", "ok": true, "detail": "connected"},
                  {"key": "push", "label": "Push to the phone", "ok": false, "detail": "FCM key missing on the server", "ignored_until": null},
                  {"key": "wake", "label": "Wake signal", "ok": false, "detail": "quiet for 3 days", "ignored_until": "2026-11-21T10:00:00+02:00"}
                ]}
                """
            )
        )
        assertEquals(listOf("push", "wake", "ha"), checks.map { it.key })
        assertTrue(checks[0].failing)
        assertFalse("ignored isn't failing", checks[1].failing)
        assertFalse(checks[2].failing)
    }
}
