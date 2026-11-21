package app.ganam.assistant.projects

import app.ganam.assistant.data.projects.Column
import app.ganam.assistant.data.projects.Entry
import app.ganam.assistant.data.projects.EntryField
import app.ganam.assistant.data.projects.Lang
import app.ganam.assistant.data.projects.Project
import app.ganam.assistant.data.projects.ProjectsCore
import app.ganam.assistant.data.projects.ProjectsJson
import app.ganam.assistant.data.projects.Section
import app.ganam.assistant.data.projects.ValueShown
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** The port of the web panel's projects-core.js: numbers, best values, order, states, countdown, budget, editor payloads. */
class ProjectsCoreTest {

    private val items get() = ProjectsJson.items(ProjectsFixture.json.getJSONObject("items_owner"))

    @Test
    fun readsNumbersLikeTheServer() {
        assertEquals(4500.0, ProjectsCore.asNumber(4500)!!, 0.0)
        assertEquals(4500.0, ProjectsCore.asNumber("4,500")!!, 0.0)
        assertEquals(13400.0, ProjectsCore.asNumber("₪ 13,400")!!, 0.0)
        assertEquals(13400.0, ProjectsCore.asNumber("13400 ש״ח")!!, 0.0)
        assertEquals(4.5, ProjectsCore.asNumber("4.5")!!, 0.0)
        assertNull(ProjectsCore.asNumber("10500-16500"))
        assertNull(ProjectsCore.asNumber("about 5k"))
        assertNull(ProjectsCore.asNumber(true))
        assertNull(ProjectsCore.asNumber(null))
    }

    @Test
    fun formatsMoney() {
        assertEquals("₪13,400", ProjectsCore.money(13400.0))
        assertEquals("-₪500", ProjectsCore.money(-500.0))
        assertEquals("₪13.4k", ProjectsCore.shortMoney(13400.0))
        assertEquals("₪150k", ProjectsCore.shortMoney(150000.0))
        assertEquals("₪900", ProjectsCore.shortMoney(900.0))
        assertEquals("4.5", ProjectsCore.number(4.5))
        assertEquals("1,200", ProjectsCore.number(1200.0))
    }

    @Test
    fun bestValuesLeaveOutRangesAndRejected() {
        val its = items
        val photo = its.sections[0]
        val best = ProjectsCore.bestIds(photo, its.entries.filter { it.sectionId == photo.id })
        // Chosen 13,400 vs shortlist 13,900; the rejected range is ignored.
        val chosen = its.entries.first { it.decision == "chosen" }
        assertEquals(setOf(chosen.id), best["e4e25675"])
        assertNull(best["9ebea31c"]) // phone: no best
        // One option alone has no best.
        val dj = its.sections[2]
        assertTrue(ProjectsCore.bestIds(dj, its.entries.filter { it.sectionId == dj.id }).isEmpty())
    }

    @Test
    fun bestOfARatingIsTheHighestAndASetBestWins() {
        val rating = Column("r", "Rating", "rating")
        val hours = Column("h", "Hours", "number", best = "high")
        val s = Section("s", "p", "S", kind = "options", columns = listOf(rating, hours))
        val a = Entry("a", "p", "s", fields = listOf(EntryField("Rating", "rating", 4), EntryField("Hours", "number", 6)))
        val b = Entry("b", "p", "s", fields = listOf(EntryField("Rating", "rating", 5), EntryField("Hours", "number", 8)))
        val c = Entry("c", "p", "s", decision = "rejected", fields = listOf(EntryField("Hours", "number", 12)))
        val best = ProjectsCore.bestIds(s, listOf(a, b, c))
        assertEquals(setOf("b"), best["r"])
        assertEquals(setOf("b"), best["h"])
        assertEquals("low", ProjectsCore.bestWay(Column("m", "Price", "money")))
        assertEquals("", ProjectsCore.bestWay(Column("t", "Notes")))
    }

    @Test
    fun ordersOptionsByDecision() {
        val e = { id: String, d: String, at: String -> Entry(id, "p", "s", decision = d, createdAt = at) }
        val order = ProjectsCore.orderOptions(
            listOf(e("r", "rejected", "1"), e("o2", "", "3"), e("c", "chosen", "5"), e("o1", "", "2"), e("s", "shortlist", "4"))
        )
        assertEquals(listOf("c", "s", "o1", "o2", "r"), order.map { it.id })
    }

    @Test
    fun sectionStatesInBothLanguages() {
        val its = items
        val photo = its.sections[0]
        val st = ProjectsCore.sectionState(photo, its.entries, Lang.HE)
        assertEquals(3, st.count)
        assertEquals("נבחר: אורי עשור", st.line)
        assertEquals("₪13.4k–₪13.9k", ProjectsCore.priceRange(st))
        assertEquals("אפשרות אחת · 1 ברשימה הקצרה · טרם הוחלט", ProjectsCore.sectionState(its.sections[2], its.entries, Lang.HE).line)
        assertEquals("1 option · 1 shortlisted · not decided", ProjectsCore.sectionState(its.sections[2], its.entries, Lang.EN).line)
        assertEquals("עדיין ריק", ProjectsCore.sectionState(its.sections[1], its.entries, Lang.HE).line)
        assertEquals("No options yet", ProjectsCore.sectionState(Section("x", "p", "X", kind = "options"), its.entries, Lang.EN).line)
    }

    @Test
    fun countsDownToTheDate() {
        val today = LocalDate.of(2026, 11, 19)
        assertEquals("Today", ProjectsCore.countdown("2026-11-19", today, Lang.EN)!!.label)
        assertEquals("Tomorrow", ProjectsCore.countdown("2026-11-20", today, Lang.EN)!!.label)
        assertEquals("in 305 days", ProjectsCore.countdown("2027-09-20", today, Lang.EN)!!.label)
        assertEquals("בעוד 305 ימים", ProjectsCore.countdown("2027-09-20", today, Lang.HE)!!.label)
        assertEquals("2 days ago", ProjectsCore.countdown("2026-11-17", today, Lang.EN)!!.label)
        assertEquals("20.9.2027", ProjectsCore.countdown("2027-09-20", today, Lang.EN)!!.date)
        assertNull(ProjectsCore.countdown("soon", today, Lang.EN))
        assertNull(ProjectsCore.countdown(null, today, Lang.EN))
    }

    @Test
    fun budgetCommitsTheChosenPrices() {
        val its = items
        val fromItems = ProjectsCore.budgetInfo(its.project, its.sections, its.entries)!!
        assertEquals(13400.0, fromItems.committed, 0.0)
        assertEquals(150000.0, fromItems.budget!!, 0.0)
        assertEquals(136600.0, fromItems.left!!, 0.0)
        assertEquals(9, fromItems.pct)
        val fromList = ProjectsCore.budgetInfo(ProjectsJson.projects(ProjectsFixture.json.getJSONArray("list_owner"))[0])!!
        assertEquals(13400.0, fromList.committed, 0.0)
        assertNull(ProjectsCore.budgetInfo(Project("p", "Empty")))
        assertEquals(100, ProjectsCore.budgetInfo(Project("p", "Over", budget = 100.0, committed = 250.0))!!.pct)
    }

    @Test
    fun setsAFieldTypedAsItsColumn() {
        val price = Column("k", "Price", "money")
        val fields = listOf(EntryField("Phone", "phone", "050"), EntryField("price", "money", 1))
        val set = ProjectsCore.setField(fields, price, "₪ 4,500")
        assertEquals(2, set.size)
        assertEquals("price", set[1].name) // keeps the stored name
        assertEquals(4500L, set[1].value)
        assertEquals(4.5, ProjectsCore.setField(emptyList(), Column("n", "Rooms", "number"), "4.5")[0].value)
        assertEquals("a range", ProjectsCore.setField(emptyList(), price, "a range")[0].value)
        assertEquals(true, ProjectsCore.setField(emptyList(), Column("c", "Video", "checkbox"), true)[0].value)
        assertEquals("x", ProjectsCore.setField(emptyList(), Column("t", "Note"), " x ")[0].value)
    }

    @Test
    fun extraFieldsAndPhones() {
        val s = Section("s", "p", "S", kind = "options", columns = listOf(Column("k", "Price", "money")))
        val e = Entry("e", "p", "s", fields = listOf(EntryField("price", "money", 1), EntryField("טלפון", "text", "052-1234567")))
        assertEquals(listOf("טלפון"), ProjectsCore.extraFields(e, s).map { it.name })
        val phone = ProjectsCore.firstPhone(e, s)!!
        assertEquals("052-1234567", phone.value)
        assertEquals("0521234567", phone.tel)
        assertEquals("972521234567", phone.wa)
        assertNull(ProjectsCore.phoneLinks("12"))
        assertEquals("972501112222", ProjectsCore.phoneLinks("+972 50 111 2222")!!.wa)
    }

    @Test
    fun showsValuesByType() {
        assertEquals(ValueShown.Style.NONE, ProjectsCore.shown(Column("k", "P", "money"), "", Lang.EN).style)
        assertEquals("₪13,400", ProjectsCore.shown(Column("k", "P", "money"), 13400, Lang.EN).text)
        assertEquals(3, ProjectsCore.shown(Column("k", "R", "rating"), 3, Lang.EN).stars)
        assertEquals("✓ כן", ProjectsCore.shown(Column("k", "V", "checkbox"), true, Lang.HE).text)
        val url = ProjectsCore.shown(Column("k", "Site", "url"), "www.example.com/a", Lang.EN)
        assertEquals("example.com", url.text)
        assertEquals("https://www.example.com/a", url.link)
        assertEquals("", ProjectsCore.safeUrl("javascript:alert(1)"))
        assertEquals("tel:0520000000", ProjectsCore.shown(Column("k", "Ph", "phone"), "052-0000000", Lang.EN).link)
        assertEquals("1.2.2027", ProjectsCore.shown(Column("k", "D", "date"), "2027-02-01", Lang.EN).text)
    }

    @Test
    fun columnsPayloadCleansTheEditorRows() {
        val rows = listOf(
            Column("k1", " Price ", "money", role = "price"),
            Column("", "", "text"),
            Column("k2", "price", "number"),
            Column("", "Menu", "select", options = listOf(" Meat", "", "Vegan ")),
            Column("", "Odd", "weird", role = "x", options = listOf("a"))
        )
        val out = ProjectsCore.columnsPayload(rows)
        assertEquals(listOf("Price", "Menu", "Odd"), out.map { it.name })
        assertEquals("k1", out[0].key)
        assertEquals(listOf("Meat", "Vegan"), out[1].options)
        assertEquals("text", out[2].type)
        assertEquals("", out[2].role)
        assertTrue(out[2].options.isEmpty())
    }

    @Test
    fun smallHelpers() {
        assertEquals("1.4 MB", ProjectsCore.fileSize(1468006))
        assertEquals("3 KB", ProjectsCore.fileSize(3000))
        assertEquals("14 B", ProjectsCore.fileSize(14))
        assertEquals("pdf", ProjectsCore.fileKind("application/pdf"))
        assertEquals("image", ProjectsCore.fileKind("image/png"))
        assertEquals("TL;DR a b", ProjectsCore.preview("## TL;DR\n- **a** b"))
        assertEquals(10, ProjectsCore.preview("x".repeat(50), 10).length)
        assertEquals(Lang.HE, ProjectsCore.langOf(items.project, items.sections))
        assertEquals(Lang.EN, ProjectsCore.langOf(Project("p", "Trip"), emptyList()))
        assertEquals(Lang.HE, ProjectsCore.langOf(Project("p", "Trip"), listOf(Section("s", "p", "דירות"))))
        val list = listOf(Project("a", "A", status = "archived"), Project("b", "B"), Project("c", "C"))
        assertEquals(listOf("b", "c", "a"), ProjectsCore.orderProjects(list).map { it.id })
        val p = ProjectsJson.projects(ProjectsFixture.json.getJSONArray("list_owner"))[0]
        assertEquals("בעוד 305 ימים · 1/2 הוחלטו", ProjectsCore.projectLine(p, LocalDate.of(2026, 11, 19)))
        assertEquals("3 items", ProjectsCore.projectLine(Project("p", "Garden", entryCount = 3), LocalDate.of(2026, 11, 19)))
    }
}
