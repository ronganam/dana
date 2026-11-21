package io.github.ronganam.dana.shopping

import io.github.ronganam.dana.data.shopping.AddResult
import io.github.ronganam.dana.data.shopping.ShoppingJson
import io.github.ronganam.dana.data.shopping.ShoppingOp
import io.github.ronganam.dana.data.shopping.ShoppingOps
import io.github.ronganam.dana.data.shopping.ShoppingText
import io.github.ronganam.dana.data.shopping.ShoppingView
import io.github.ronganam.dana.data.shopping.Who
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Parsing real payloads, the screen model (aisles, lingering ticks, words) and the typed-text parser. */
class ShoppingModelTest {
    private val noa = Who("d3b2f55f2a70", "Noa Peretz")
    private val tom = Who("be9d56b897fa", "Tom Hadad")

    // ---------- parsing ----------

    @Test
    fun parsesTheSharedOwnersScreen() {
        val s = ShoppingFixture.screen("owner_shared")
        assertEquals("a142b3bd0785", s.list.id)
        assertEquals("owner", s.list.access)
        assertTrue(s.list.isOwner && s.list.canEdit)
        assertEquals(20L, s.list.version)
        assertEquals(listOf("owner", "edit", "read"), s.list.people.map { it.access })
        assertEquals("Noa Peretz", s.list.owner?.name)
        assertEquals(13, s.categories.size)
        assertEquals("produce", s.categories.first().key)
        assertEquals("other", s.categories.last().key)
        assertEquals(14, s.items.size)
        assertEquals(12, s.counts.toBuy)
        assertEquals(2, s.counts.bought)
        assertEquals(listOf("Oat milk", "Pita"), s.usuals.map { it.name })
        val bananas = s.items.first { it.name == "bananas" }
        assertEquals("1 kg", bananas.quantity)
        assertEquals("produce", bananas.category)
        assertEquals(noa, bananas.addedBy)
        assertEquals("Tom Hadad", s.items.first { it.name == "Pasta" }.addedBy?.name)
        assertTrue(s.items.first { it.name == "Eggs" }.checked)
        assertTrue(s.isActive)
    }

    @Test
    fun everyScreenPayloadParses() {
        val screens = ShoppingFixture.keys().filter { ShoppingFixture.obj(it).containsKey("list") }
        assertTrue(screens.size >= 15)
        for (k in screens) {
            val s = ShoppingJson.screen(ShoppingFixture.body(k))
            assertNotNull(k, s)
            assertEquals(k, s!!.counts.toBuy, s.items.count { !it.checked })
        }
        assertNull(ShoppingJson.screen(ShoppingFixture.body("lists")))
    }

    @Test
    fun parsesWriteAnswersWithUndoAndResults() {
        val add = ShoppingJson.answer(ShoppingFixture.body("add_mixed"))!!
        assertEquals(listOf("already", "restored", "added"), add.results.map { it.status })
        assertEquals("d08ea160b089", add.undo?.id)
        assertEquals(6L, add.screen.list.version)
        val check = ShoppingJson.answer(ShoppingFixture.body("check"))!!
        assertEquals("Bought 'Eggs'", check.undo?.label)
        assertTrue(check.results.isEmpty())
        assertNull(ShoppingJson.answer(ShoppingFixture.body("error_duplicate")))
    }

    @Test
    fun parsesTheReadOnlyAndMemberViews() {
        val viewer = ShoppingFixture.screen("viewer")
        assertFalse(viewer.list.canEdit)
        assertEquals("read", viewer.invites.single().access)
        val member = ShoppingFixture.screen("member_shared")
        assertTrue(member.list.canEdit)
        assertFalse(member.list.isOwner)
        assertTrue(member.isActive)
        assertEquals(2, member.lists.size)
    }

    @Test
    fun parsesSuggestionsAndTheShareSheet() {
        assertEquals(listOf("Hummus"), ShoppingJson.usuals(ShoppingFixture.body("suggestions")).map { it.name })
        val share = ShoppingJson.share(ShoppingFixture.body("share_get"))
        assertEquals(listOf("edit", "read"), share.sharedWith.map { it.access })
        assertEquals("Tom Hadad", share.sharedWith[0].name)
        val model = ShoppingView.share(share)
        assertTrue(model.others.none { o -> share.sharedWith.any { it.id == o.id } })
        assertTrue(model.others.isNotEmpty())
    }

    // ---------- the screen model ----------

    @Test
    fun groupsByTheServersAisleOrder() {
        val m = ShoppingView.model(ShoppingFixture.screen("owner_shared"), noa)
        assertEquals(listOf("produce", "bakery", "dairy", "pantry", "household", "other"), m.groups.map { it.key })
        assertEquals("Fruit & vegetables", m.groups[0].name)
        assertEquals(listOf("עגבניות שרי", "bananas", "Avocado"), m.groups[0].rows.map { it.name })
        assertEquals(listOf("Eggs", "Hummus").toSet(), m.bought.map { it.name }.toSet())
        assertEquals(12, m.toBuy)
        assertFalse(m.empty)
        assertTrue(m.shared)
        assertEquals(3, m.people.size)
    }

    @Test
    fun anUnknownAisleGoesUnderOther() {
        val s = ShoppingFixture.screen("owner_alone")
        val odd = s.copy(items = s.items.mapIndexed { i, it -> if (i == 0) it.copy(category = "spaceship") else it })
        val m = ShoppingView.model(odd, noa)
        assertTrue(m.groups.last { it.key == "other" }.rows.any { it.id == s.items[0].id })
    }

    @Test
    fun whoAddedShowsOnlyOnSharedListsAndNeverForMe() {
        val asNoa = ShoppingView.model(ShoppingFixture.screen("owner_shared"), noa)
        val rows = asNoa.groups.flatMap { it.rows }
        assertEquals(tom, rows.first { it.name == "Pasta" }.by)
        assertNull(rows.first { it.name == "bananas" }.by)
        val asTom = ShoppingView.model(ShoppingFixture.screen("member_shared"), tom)
        assertEquals(noa, asTom.groups.flatMap { it.rows }.first { it.name == "bananas" }.by)
        val alone = ShoppingView.model(ShoppingFixture.screen("owner_alone"), noa)
        assertTrue(alone.groups.flatMap { it.rows }.all { it.by == null })
    }

    @Test
    fun aTickedRowLingersInPlaceThenMovesToBought() {
        val s = ShoppingFixture.screen("owner_shared")
        val bananas = s.items.first { it.name == "bananas" }
        val anchor = ShoppingView.anchorAfter(s, bananas.id)
        assertEquals(s.items.first { it.name == "Avocado" }.id, anchor)
        val ticked = ShoppingOps.applyOps(s, listOf(ShoppingOp.Check("o1", s.list.id, bananas.id, true, "bananas")), noa, "2026-11-16T15:00:00Z")!!
        val lingering = ShoppingView.model(ticked, noa, mapOf(bananas.id to anchor))
        val produce = lingering.groups.first { it.key == "produce" }.rows
        assertEquals(listOf("עגבניות שרי", "bananas", "Avocado"), produce.map { it.name })
        assertTrue(produce[1].lingering && produce[1].checked)
        assertTrue(lingering.bought.none { it.id == bananas.id })
        val settled = ShoppingView.model(ticked, noa)
        assertEquals(listOf("עגבניות שרי", "Avocado"), settled.groups.first { it.key == "produce" }.rows.map { it.name })
        assertEquals("bananas", settled.bought.first().name)
        // The last of its aisle stays at the end of the aisle.
        val avocado = s.items.first { it.name == "Avocado" }
        assertNull(ShoppingView.anchorAfter(s, avocado.id))
    }

    @Test
    fun subtitlesSayWhoseListAndHowMuch() {
        assertEquals("12 to buy · Shared with Tom and Gal", ShoppingView.subtitle(ShoppingFixture.screen("owner_shared"), noa))
        assertEquals("Noa's list · 12 to buy", ShoppingView.subtitle(ShoppingFixture.screen("member_shared"), tom))
        assertEquals("View only · Noa's list", ShoppingView.subtitle(ShoppingFixture.screen("viewer"), null))
        val alone = ShoppingFixture.screen("owner_alone")
        assertEquals("${alone.counts.toBuy} to buy", ShoppingView.subtitle(alone, noa))
        assertEquals("Nothing to buy", ShoppingView.subtitle(alone.copy(items = emptyList(), counts = alone.counts.copy(toBuy = 0)), noa))
    }

    @Test
    fun theMenuOffersOnlyWhatItCan() {
        val alone = ShoppingFixture.screen("owner_alone")
        val aloneMenu = ShoppingView.menuItems(alone)
        assertTrue(aloneMenu.none { it.act == "switch" || it.act == "view" })
        val member = ShoppingView.menuItems(ShoppingFixture.screen("member_shared"))
        assertEquals(listOf("Your list", "Noa's list"), member.filter { it.act == "switch" }.map { it.label })
        assertTrue(member.first { it.label == "Noa's list" }.current)
        assertEquals("Clear 2 bought items", member.last().label)
        assertTrue(ShoppingView.menuItems(ShoppingFixture.screen("viewer")).none { it.act == "clear" })
    }

    @Test
    fun theInvitationAsksToMoveOwnItems() {
        val s = ShoppingFixture.screen("member_invite")
        val inv = ShoppingView.invite(s.invites.single(), s)
        assertEquals("Noa shared their shopping list with you", inv.text)
        assertTrue(inv.canUse && inv.askMove)
        assertEquals("Move your 3 items over?", inv.moveText)
        val viewer = ShoppingFixture.screen("viewer")
        val ro = ShoppingView.invite(viewer.invites.single(), viewer)
        assertTrue(ro.readOnly && !ro.canUse && !ro.askMove)
    }

    // ---------- words ----------

    @Test
    fun errorsInPlainWords() {
        fun say(k: String) = ShoppingView.errorText(ShoppingFixture.status(k), ShoppingJson.refusal(ShoppingFixture.body(k)))
        assertEquals("This list is view only.", say("error_read_only"))
        assertTrue(say("error_not_connected").startsWith("Shopping isn't turned on"))
        assertEquals("At most 30 items at a time.", say("error_too_many"))
        assertEquals("bread is already on the list.", say("error_duplicate"))
        assertTrue(say("error_not_found").startsWith("That's no longer on the list"))
        assertEquals("You're offline — nothing was changed.", ShoppingView.errorText(-1, null))
        assertTrue(ShoppingView.errorText(503, null).startsWith("Something went wrong"))
    }

    @Test
    fun undoAnswersAndLabels() {
        fun undo(k: String) = ShoppingView.undoText(ShoppingFixture.status(k), ShoppingJson.refusal(ShoppingFixture.body(k)))
        assertEquals(true to "Undone", undo("undo_ok"))
        assertEquals(false to "Couldn't undo: 'Avocado' was changed again since, so it was left as it is.", undo("undo_conflict"))
        assertEquals(false to "This can no longer be undone.", undo("undo_gone"))
        assertEquals("Bought Eggs", ShoppingView.undoLabel("Bought 'Eggs'"))
        assertEquals("Removed Avocado", ShoppingView.undoLabel("Removed 'Avocado' from the shopping list"))
        assertEquals("Added Avocado and Eggs", ShoppingView.undoLabel("Added 'Avocado' and 'Eggs' to the shopping list"))
        val add = ShoppingView.addResultText(listOf(AddResult("1", "חלב", "already"), AddResult("2", "Eggs", "restored"), AddResult("3", "Avocado", "added")))
        assertEquals("Added Avocado · Eggs is back on the list · חלב is already on the list", add.text)
        assertFalse(add.obvious)
        val queued = ShoppingView.queuedLabel(ShoppingOp.Check("o", "l", "i", true, "Milk"))
        assertEquals("Bought Milk · will sync", queued)
    }

    @Test
    fun theEditorStepsAndSavesOnlyWhatChanged() {
        assertEquals("2", ShoppingView.stepQuantity("", 1))
        assertEquals("", ShoppingView.stepQuantity("2", -1))
        assertEquals("", ShoppingView.stepQuantity("", -1))
        assertEquals("5", ShoppingView.stepQuantity("4", 1))
        assertEquals("999", ShoppingView.stepQuantity("999", 1))
        assertEquals("1 kg", ShoppingView.stepQuantity("1 kg", 1))
        assertFalse(ShoppingView.isCount("1 kg"))
        val item = ShoppingFixture.screen("owner_shared").items.first { it.name == "Avocado" }
        val d = ShoppingView.draft(item)
        assertEquals(emptyMap<String, String>(), ShoppingView.editChanges(item, d).getOrThrow())
        assertEquals(
            mapOf("quantity" to "4", "category" to "pantry"),
            ShoppingView.editChanges(item, d.copy(quantity = " 4 ", category = "pantry")).getOrThrow()
        )
        assertTrue(ShoppingView.editChanges(item, d.copy(name = "   ")).isFailure)
    }

    // ---------- the typed-text parser (the server's own table, integrations/shopping/names.py) ----------

    @Test
    fun parsesTypedTextLikeTheServer() {
        val table = listOf(
            "2 חלב" to listOf("חלב" to "2"), "3x eggs" to listOf("eggs" to "3"), "3 x eggs" to listOf("eggs" to "3"),
            "1 kg tomatoes" to listOf("tomatoes" to "1 kg"), "1kg tomatoes" to listOf("tomatoes" to "1 kg"),
            "2 ליטר חלב" to listOf("חלב" to "2 ליטר"), "500 גרם גבינה צהובה" to listOf("גבינה צהובה" to "500 גרם"),
            "eggs x2" to listOf("eggs" to "2"), "ביצים ×12" to listOf("ביצים" to "12"), "milk ×2" to listOf("milk" to "2"),
            "1% milk" to listOf("1% milk" to ""), "2 gala apples" to listOf("gala apples" to "2"),
            "milk, bread\n- butter\n\n" to listOf("milk" to "", "bread" to "", "butter" to ""),
            "1. milk\n2) bread" to listOf("milk" to "", "bread" to ""),
            "חלב, לחם,  ביצים" to listOf("חלב" to "", "לחם" to "", "ביצים" to ""),
            "3 בקבוקים מים" to listOf("מים" to "3 בקבוקים"), "x2" to listOf("x2" to "")
        )
        for ((text, want) in table) {
            assertEquals(text, want, ShoppingText.parse(text).map { it.name to it.quantity })
        }
    }

    @Test
    fun nameKeysMatchTheServer() {
        val keys = listOf(
            "Milk" to "milk", "  MILK  " to "milk", "milk   3%" to "milk 3", "חָלָב" to "חלב", "שׁוֹקוֹ" to "שוקו",
            "Coca-Cola" to "coca cola", "coca cola" to "coca cola", "ק\"ג" to "קג", "tea." to "tea", "קוטג'" to "קוטג"
        )
        for ((name, want) in keys) assertEquals(name, want, ShoppingText.nameKey(name))
    }

    @Test
    fun patternsUseOnlySyntaxAndroidsRegexEngineAccepts() {
        // Android compiles patterns with ICU, which refuses inline flags such as (?U) that the JVM accepts
        // (it crashed the place once); Unicode classes are spelled out instead (\p{Z}, \p{Nd}).
        val patterns = ShoppingText::class.java.declaredFields
            .filter { it.type == Regex::class.java }
            .map { f -> f.isAccessible = true; (f.get(ShoppingText) as Regex).pattern }
        assertTrue(patterns.size >= 6)
        for (p in patterns) assertFalse(p, Regex("""\(\?[a-zA-Z]+\)""").containsMatchIn(p))
        assertEquals(listOf("milk" to ""), ShoppingText.parse("milk\u00A0").map { it.name to it.quantity })
        assertEquals(listOf("eggs" to "٣"), ShoppingText.parse("٣ eggs").map { it.name to it.quantity })
    }

    @Test
    fun pastedLinesBecomeOneLineAndInitialsAreShort() {
        assertEquals("milk, bread, butter", ShoppingText.pasteAsLine("milk\n bread\r\n\nbutter"))
        assertEquals("milk", ShoppingText.pasteAsLine("milk"))
        assertEquals("NP", ShoppingText.initials("Noa Peretz"))
        assertEquals("G", ShoppingText.initials("Gal"))
        assertEquals("מל", ShoppingText.initials("מאיה לוי"))
        assertEquals("Someone", ShoppingText.firstName("  "))
    }
}
