package io.github.ronganam.dana.now

import io.github.ronganam.dana.data.models.NowBlock
import io.github.ronganam.dana.data.models.NotificationsOverview
import io.github.ronganam.dana.data.models.OverviewApp
import io.github.ronganam.dana.data.models.OverviewCounts
import io.github.ronganam.dana.data.models.PhoneNotification
import io.github.ronganam.dana.data.models.SearchResult
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.NotificationsApi
import io.github.ronganam.dana.data.network.NowApi
import io.github.ronganam.dana.data.network.OverviewWire
import io.github.ronganam.dana.data.network.SearchWire
import io.github.ronganam.dana.data.network.text
import io.github.ronganam.dana.ui.screens.common.isolatedParts
import io.github.ronganam.dana.ui.screens.notifications.summaryPoint
import io.github.ronganam.dana.ui.screens.now.ItemDraft
import io.github.ronganam.dana.ui.screens.now.NowGlyph
import io.github.ronganam.dana.ui.screens.now.NowVisual
import io.github.ronganam.dana.ui.screens.now.NowModel
import io.github.ronganam.dana.ui.screens.now.PendingRemovals
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.viewmodels.AppRule
import io.github.ronganam.dana.ui.viewmodels.OverviewMath
import io.github.ronganam.dana.ui.viewmodels.SearchController
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NowModelTest {

    /** Shaped like `GET /api/now` (server/core/now.py): a suggestion with choices + checks, a reminder, a notification, junk. */
    private val payloadJson = """
    {
      "now": "2026-11-14T18:40:00+02:00",
      "lede": "Two things need you.",
      "items": [
        {
          "id": "sug:41", "kind": "suggestion", "source": "Call · Avi · 18:02", "title": "Book the plumber",
          "why": "You said you'd call him back.", "schedulable": true,
          "ref": {"type": "call", "id": "c7"},
          "primary": {"action": "accept", "label": "Add reminder", "args": {"source": "call"}},
          "secondary": [{"action": "edit", "label": "Edit"}, {"action": "dismiss", "label": "Dismiss"}],
          "blocks": [
            {"type": "lines", "label": "From the call", "lines": [{"text": "אני אתקשר מחר", "who": "Avi", "hit": true}, {"text": "ok"}]},
            {"type": "choices", "key": "when", "options": [
              {"label": "Tomorrow 9:00", "value": {"time": "2026-11-15T09:00:00"}, "selected": true},
              {"label": "At home", "value": {"location": "home"}, "action_label": "Remind at home", "message": "When you get home."}
            ]},
            {"type": "checks", "key": "lists", "options": [
              {"label": "Home", "value": "g1", "checked": true},
              {"label": "Work", "value": "g2"},
              {"label": "Count", "value": 3}
            ]},
            {"type": "hologram", "label": "From the future"},
            {"type": "week", "cells": [{"label": "Mon", "value": "7h", "ok": true}, {"label": "Tue", "ok": false}, {"label": "Wed"}]}
          ]
        },
        {
          "id": "task:9", "kind": "reminder", "source": "Reminder · 18:30", "title": "לקנות חלב",
          "primary": {"action": "done", "label": "Done"},
          "secondary": [
            {"action": "snooze", "label": "1 h", "args": {"minutes": 60}},
            {"action": "snooze", "label": "Tonight", "args": {"slot": "21"}},
            {"action": "snooze", "label": "Tomorrow", "args": {"slot": "tomorrow"}},
            {"action": "dismiss", "label": "Skip"}
          ],
          "blocks": [{"type": "facts", "items": ["Due 18:30", null, "List: Home"]}, {"type": "text", "text": "From Dana"}]
        },
        {
          "id": "notif:5", "kind": "notification", "title": "Mom", "why": "Dana thinks this needs you.",
          "primary": {"action": "reply", "label": "Reply", "client": true},
          "secondary": [{"action": "open", "label": "Open", "client": true}, {"action": "dismiss", "label": "Clear"}],
          "notification": {"id": "5", "key": "0|com.whatsapp|1", "package": "com.whatsapp", "app": "WhatsApp",
                           "can_reply": true, "actions": [{"title": "Mark as read"}, {"title": "Reply", "reply": true}]},
          "blocks": [{"type": "chat", "messages": [{"text": "את באה?", "who": "Mom"}, {"text": "soon", "mine": true}]}]
        },
        {"kind": "suggestion", "title": "no id, dropped"}
      ],
      "quiet": [{"id": "sug:50", "title": "Quiet one", "primary": {"action": "accept", "label": "Add"}, "secondary": [{"action": "dismiss", "label": "×"}]}],
      "quiet_total": 4,
      "notifications": {"total": 37, "noise": 12, "needs_you": 3},
      "day": {"calendar": false, "slots": [{"key": "19", "label": "19:00", "items": [{"title": "Gym", "kind": "event", "time": "19:30"}]}]},
      "brand_new_field": {"ignored": true}
    }
    """.trimIndent()

    private val payload = NowApi.parse(payloadJson)
    private val sug = payload.items[0]
    private val task = payload.items[1]
    private val notif = payload.items[2]

    @Test
    fun parsesThePayload() {
        assertEquals("Two things need you.", payload.lede)
        assertEquals(listOf("sug:41", "task:9", "notif:5"), payload.items.map { it.id })
        assertEquals(4, payload.quietTotal)
        assertEquals(1, payload.quiet.size)
        assertEquals(12, payload.notifications.noise)
        assertEquals(3, payload.notifications.needsYou)
        assertFalse(payload.calendarOk)
        assertEquals("19", payload.daySlots.single().key)
        assertTrue(sug.schedulable)
        assertEquals("call", sug.ref?.type)
        assertEquals("""{"source":"call"}""", sug.primary?.args.toString())
        assertTrue(notif.primary!!.client)
        assertEquals("com.whatsapp", notif.notification?.packageName)
        assertEquals("Reply", NowModel.replyButton(notif))
        assertEquals(listOf("Due 18:30", "List: Home"), (task.blocks[0] as NowBlock.Facts).items)
    }

    @Test
    fun unknownBlocksAreKeptAsUnknown() {
        val unknown = sug.blocks.filterIsInstance<NowBlock.Unknown>().single()
        assertEquals("hologram", unknown.type)
        assertEquals("From the future", unknown.label)
        val week = sug.blocks.filterIsInstance<NowBlock.Week>().single()
        assertEquals(listOf(true, false, null), week.cells.map { it.ok })
        assertEquals("–", week.cells[1].value)
        val chat = notif.blocks.single() as NowBlock.Chat
        assertTrue(chat.messages[1].mine)
        val lines = sug.blocks[0] as NowBlock.Lines
        assertTrue(lines.lines[0].hit)
        assertEquals("Avi", lines.lines[0].who)
    }

    @Test
    fun argsComeFromTheChoicesAndChecks() {
        // Defaults: the selected choice, the checked options, plus the action's own args.
        val a = NowModel.argsFor(sug, sug.primary!!, null, isPrimary = true)
        assertEquals("call", a.text("source"))
        assertEquals("2026-11-15T09:00:00", (a["when"] as JsonObject).text("time"))
        assertEquals(listOf<Any>("g1"), (a["lists"] as JsonArray).values())

        // The owner picked "At home" and ticked Work and the number.
        val draft = ItemDraft(choices = mapOf("when" to 1), checks = mapOf("lists" to setOf(1, 2)))
        val b = NowModel.argsFor(sug, sug.primary!!, draft, isPrimary = true)
        assertEquals("home", (b["when"] as JsonObject).text("location"))
        assertEquals(listOf<Any>("g2", 3), (b["lists"] as JsonArray).values())
        assertEquals("Remind at home", NowModel.primaryLabel(sug, draft))
        assertEquals("Add reminder", NowModel.primaryLabel(sug, null))

        // A secondary sends only its own args.
        val snooze = NowModel.argsFor(task, task.secondary[0], draft, isPrimary = false)
        assertEquals(60, (snooze["minutes"] as JsonPrimitive).intOrNull)
        assertFalse(snooze.containsKey("when"))

        // Out-of-range picks fall back to the server's defaults.
        val stale = ItemDraft(choices = mapOf("when" to 9), checks = mapOf("lists" to setOf(7)))
        val c = NowModel.argsFor(sug, sug.primary!!, stale, isPrimary = true)
        assertEquals("2026-11-15T09:00:00", (c["when"] as JsonObject).text("time"))
        assertEquals(0, (c["lists"] as JsonArray).size)
    }

    @Test
    fun rowActionsAndDismiss() {
        val s = NowModel.rowActions(sug)
        assertEquals("accept", s.primary?.action)
        assertEquals(listOf("edit", "dismiss"), s.overflow.map { it.action })
        assertEquals("dismiss", s.dismiss?.action)

        // A reminder with four others: only Done shows; every snooze and Skip wait in the overflow, in server order.
        val t = NowModel.rowActions(task)
        assertEquals("done", t.primary?.action)
        assertEquals(listOf("1 h", "Tonight", "Tomorrow", "Skip"), t.overflow.map { it.label })
        assertEquals("Skip", t.dismiss?.label)
        assertEquals("1 h", t.snooze?.label)
    }

    @Test
    fun slotKeysAndNextSelection() {
        assertEquals("21", NowModel.slotKey(DaySlot.Hour(21)))
        assertEquals("home", NowModel.slotKey(DaySlot.AtHome))
        assertEquals("wake", NowModel.slotKey(DaySlot.OnWaking))

        val ids = listOf("a", "b", "c")
        assertEquals("c", NowModel.nextSelection(ids, "b"))
        assertEquals("b", NowModel.nextSelection(ids, "c"))
        assertEquals("b", NowModel.nextSelection(ids, "a"))
        assertNull(NowModel.nextSelection(listOf("a"), "a"))
    }

    @Test
    fun actResultParsesBothKindsOfDeviceKeys() {
        val dismissed = NotificationsApi.actResult(obj("""{"message":"Cleared","undo_id":"u1","device_keys":["k1","",null]}"""))
        assertEquals("Cleared", dismissed.message)
        assertEquals("u1", dismissed.undoId)
        assertEquals(listOf("k1"), dismissed.cancelKeys)
        val snoozed = NotificationsApi.actResult(obj("""{"message":"Snoozed","device_keys":[{"key":"k2","until_ms":1763140000000}]}"""))
        assertEquals(mapOf("k2" to 1763140000000L), snoozed.snoozeKeys)
        assertNull(snoozed.undoId)
        assertEquals("Done", NotificationsApi.actResult(obj("{}")).message)
    }

    @Test
    fun optimisticRemovalAndUndo() {
        val p = PendingRemovals()
        val items = payload.items

        // Acted on: hidden at once.
        p.add("task:9")
        assertEquals(listOf("sug:41", "notif:5"), p.visible(items).map { it.id })

        // A refresh that started before the action finished must not bring it back.
        val early = p.refreshStarted()
        p.finished("task:9")
        p.refreshed(early)
        assertEquals(listOf("sug:41", "notif:5"), p.visible(items).map { it.id })

        // One that started after: the server's answer wins (still there = shown again).
        val late = p.refreshStarted()
        p.refreshed(late)
        assertEquals(3, p.visible(items).size)

        // Failure / Undo: back at once, even before a refresh.
        p.add("sug:41")
        p.finished("sug:41")
        p.restore("sug:41")
        assertEquals(3, p.visible(items).size)
        assertTrue(p.ids.isEmpty())

        // Still running when a refresh comes back: stays hidden.
        p.add("notif:5")
        p.refreshed(p.refreshStarted())
        assertEquals(setOf("notif:5"), p.ids)
    }

    // --- Notifications screen -------------------------------------------------------------

    private fun app(pkg: String, count: Int, noise: Int = 0, needs: Int = 0, rule: String = "normal") =
        OverviewApp(pkg, pkg.uppercase(), rule, if (rule == "quiet") "mute" else null, count, needs, noise, emptyList(), null, null)

    private val overview = NotificationsOverview(
        counts = OverviewCounts(total = 9, noise = 4, needsYou = 2),
        apps = listOf(app("gmail", 5, noise = 2, needs = 2), app("x", 2, noise = 2)),
        quietApps = listOf(app("ig", 2, noise = 0, rule = "quiet")),
        ruleSuggestions = emptyList(),
        summariesRefreshing = false
    )

    @Test
    fun overviewParsing() {
        val o = overview(("""
            {"counts": {"total": 3, "noise": 1, "needs_you": 1, "snoozed": 2},
             "apps": [{"package": "com.google.android.gm", "app": "Gmail", "rule": "alert", "rule_action": "alert",
                       "count": 3, "needs_you": 1, "noise": 1, "summary": ["אחת", "Two"], "summary_source": "ai"}, {"app": "no package"}],
             "quiet_apps": [], "rule_suggestions": [{"package": "com.x", "app": "X", "reason": "You cleared 12."}],
             "summaries_refreshing": true}
        """.trimIndent()))
        assertEquals(2, o.counts.snoozed)
        assertEquals(1, o.apps.size)
        assertEquals("alert", o.apps[0].rule)
        assertEquals(listOf("אחת", "Two"), o.apps[0].summary)
        assertEquals("You cleared 12.", o.ruleSuggestions.single().reason)
        assertTrue(o.summariesRefreshing)
    }

    @Test
    fun clearingTheNoiseRecounts() {
        val o = OverviewMath.withoutNoise(overview)
        assertEquals(listOf("gmail"), o.apps.map { it.packageName })   // X was all noise
        assertEquals(3, o.counts.total)
        assertEquals(0, o.counts.noise)
        assertEquals(2, o.counts.needsYou)

        val w = OverviewMath.withoutApp(overview, "gmail")
        assertEquals(listOf("x"), w.apps.map { it.packageName })
        assertEquals(0, w.counts.needsYou)
    }

    @Test
    fun clearingOneAndRules() {
        val n = PhoneNotification(
            id = "1", key = "k", packageName = "gmail", appName = "Gmail", title = "Hi", text = null, sender = null,
            conversation = null, isGroup = false, postedAt = null, status = "active", removedReason = null,
            triageStatus = "done", category = null, categoryEmoji = "", priority = "important", bucket = "needs_you",
            escalated = false, suggestions = emptyList()
        )
        val o = OverviewMath.withoutOne(overview, n)
        assertEquals(4, o.apps.first { it.packageName == "gmail" }.count)
        assertEquals(1, o.counts.needsYou)

        val quiet = OverviewMath.withRule(overview, "gmail", AppRule.QUIET)
        assertEquals(listOf("x"), quiet.apps.map { it.packageName })
        assertEquals("gmail", quiet.quietApps.first().packageName)
        assertEquals(0, quiet.counts.needsYou)

        // A quiet app with nothing waiting goes away when it stops being quiet (the server does the same).
        val emptyQuiet = overview.copy(quietApps = listOf(app("ig", 0, rule = "quiet")))
        val normal = OverviewMath.withRule(emptyQuiet, "ig", AppRule.NORMAL)
        assertTrue(normal.apps.none { it.packageName == "ig" } && normal.quietApps.isEmpty())

        assertEquals(AppRule.NORMAL, AppRule.of("something new"))
        assertEquals("mute", AppRule.QUIET.serverAction)
    }

    @Test
    fun searchGroupsByKind() {
        assertFalse(SearchController.isSearchable(" a "))
        assertTrue(SearchController.isSearchable("חל"))
        val r = listOf(
            SearchResult("note", "e1", "Plumber notes", "House"),
            SearchResult("reminder", "t1", "Call the plumber", "Tomorrow"),
            SearchResult("mystery", "m1", "?", ""),
            SearchResult("reminder", "t2", "Pay the plumber", ""),
            SearchResult("person", "p1", "Avi", "Plumber")
        )
        val g = SearchController.grouped(r)
        assertEquals(listOf("reminder", "person", "note", "mystery"), g.map { it.first })
        assertEquals(listOf("t1", "t2"), g[0].second.map { it.id })
        val parsed = searchResults(("""{"results":[{"kind":"call","id":"c1","title":"Call with Avi"},{"kind":"x"}]}"""))
        assertEquals(listOf(SearchResult("call", "c1", "Call with Avi", "")), parsed)
        // Routing extras: a notification opens its app's page, a note its project (null = none).
        val routed = searchResults(("""{"results":[
            {"kind":"notification","id":"n1","title":"Running late","subtitle":"WhatsApp · Gal","package":"com.whatsapp"},
            {"kind":"note","id":"e1","title":"Wi-Fi","subtitle":"Home","project_id":"p9"},
            {"kind":"note","id":"e2","title":"Loose","subtitle":"Note","project_id":null}]}"""))
        assertEquals("com.whatsapp", routed[0].packageName)
        assertEquals("p9", routed[1].projectId)
        assertEquals(null, routed[2].projectId)
    }

    @Test
    fun summaryPointStripsTheModelsNeedsFlag() {
        assertEquals("גל שואל מתי אתה מגיע" to true, summaryPoint("גל שואל מתי אתה מגיע [needs Robin]"))
        assertEquals("Build failed on main" to false, summaryPoint("Build failed on main"))
    }

    @Test
    fun isolatedPartsKeepsEachPartInItsOwnDirection() {
        val s = isolatedParts("WHATSAPP · גל · 7 MIN AGO")
        assertEquals("\u2068WHATSAPP\u2069 \u200E·\u200E \u2068גל\u2069 \u200E·\u200E \u20687 MIN AGO\u2069", s)
        assertEquals("\u2068one\u2069", isolatedParts("one"))
    }

    @Test
    fun tilesSayWhereAnItemCameFrom() {
        // A notification carries its exact package: the app's own icon, glyph only as the fallback.
        assertEquals(NowVisual.App("com.whatsapp", "WhatsApp", NowGlyph.NOTIFICATION), NowModel.visual(notif))
        // A call suggestion is a phone, even though it has choices and a time.
        assertEquals(NowVisual.Glyph(NowGlyph.CALL), NowModel.visual(sug))
        assertEquals(NowVisual.Glyph(NowGlyph.REMINDER), NowModel.visual(task))
        fun item(json: String) = NowApi.parse("""{"items":[$json]}""").items.single()
        assertEquals(NowVisual.Glyph(NowGlyph.APPROVAL), NowModel.visual(item("""{"id":"approval:main","kind":"approval","title":"x","ref":{"type":"thread","id":"main"}}""")))
        assertEquals(NowVisual.Glyph(NowGlyph.CHECK), NowModel.visual(item("""{"id":"check:wake","kind":"check","title":"x"}""")))
        assertEquals(NowVisual.Glyph(NowGlyph.REVIEW), NowModel.visual(item("""{"id":"review:1","kind":"review","title":"x"}""")))
        // Suggestions from a notification only know the app's name (the start of `source`).
        assertEquals(
            NowVisual.App(null, "Slack", NowGlyph.NOTIFICATION),
            NowModel.visual(item("""{"id":"sug:2","kind":"suggestion","source":"Slack · 2 h ago","title":"x","ref":{"type":"notification","id":"n"}}"""))
        )
        // No usable app name: the notification glyph, never a blank or a made-up app.
        assertEquals(
            NowVisual.Glyph(NowGlyph.NOTIFICATION),
            NowModel.visual(item("""{"id":"sug:3","kind":"suggestion","source":"3 min ago","title":"x","ref":{"type":"notification","id":"n"}}"""))
        )
        // Calendar suggestions and security alerts without a source app.
        assertEquals(NowVisual.Glyph(NowGlyph.EVENT), NowModel.visual(item("""{"id":"sug:4","kind":"suggestion","title":"x","primary":{"action":"accept","label":"Add to calendar · Tue 19:00"}}""")))
        assertEquals(NowVisual.Glyph(NowGlyph.ALERT), NowModel.visual(item("""{"id":"sug:5","kind":"suggestion","title":"x","primary":{"action":"dismiss","label":"Got it"}}""")))
        // Unknown kinds still get a tile.
        assertEquals(NowVisual.Glyph(NowGlyph.SUGGESTION), NowModel.visual(item("""{"id":"zzz:1","kind":"zzz","title":"x"}""")))
        // Quieter suggestions come from notifications: the app name when the source has one.
        val quiet = NowApi.parse(("""{"items":[],"quiet":[
            {"id":"sug:7","kind":"suggestion","source":"Gmail · 2 h ago","title":"x","ref":{"type":"notification","id":"n"}},
            {"id":"sug:8","kind":"suggestion","source":"Call · Avi · 18:02","title":"x","ref":{"type":"call","id":"c"}}]}""")).quiet
        assertEquals(NowVisual.App(null, "Gmail", NowGlyph.NOTIFICATION), NowModel.visual(quiet[0]))
        assertEquals(NowVisual.Glyph(NowGlyph.CALL), NowModel.visual(quiet[1]))
    }

    @Test
    fun appNameIsTheFirstPartOfAnAppAndTimeSource() {
        assertEquals("Gmail", NowModel.appNameOf("Gmail · Bright Data · 3 min ago"))
        assertEquals("Slack", NowModel.appNameOf("Slack · 2 h ago"))
        assertNull(NowModel.appNameOf("Slack"))
        assertNull(NowModel.appNameOf("Call · Avi · 18:02"))
        assertNull(NowModel.appNameOf("Reminder · 18:30"))
        assertNull(NowModel.appNameOf(""))
    }
}

private fun obj(text: String) = ApiJson.parseToJsonElement(text) as JsonObject

private fun overview(text: String): NotificationsOverview = ApiJson.decodeFromString(OverviewWire.serializer(), text).toModel()

private fun searchResults(text: String): List<SearchResult> =
    ApiJson.decodeFromString(SearchWire.serializer(), text).results.mapNotNull { it.toModel() }

/** Strings and whole numbers as their Kotlin values. */
private fun JsonArray.values(): List<Any> = map { v ->
    val p = v as JsonPrimitive
    if (p.isString) p.content else p.intOrNull ?: p.content
}
