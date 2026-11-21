package io.github.ronganam.dana.projects

import io.github.ronganam.dana.data.projects.ProjectsJson
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.long
import io.github.ronganam.dana.data.network.text
import io.github.ronganam.dana.data.projects.ColumnBody
import io.github.ronganam.dana.data.projects.FieldBody
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Parsing the server's Projects payloads and writing what PUT sends. */
class ProjectsJsonTest {

    @Test
    fun parsesTheProjectList() {
        val list = ProjectsJson.projects(ProjectsFixture.text("list_owner"))
        assertEquals(1, list.size)
        val p = list[0]
        assertEquals("חתונה", p.title)
        assertEquals("💍", p.icon)
        assertEquals("#6366f1", p.color)
        assertEquals("2027-09-20", p.targetDate)
        assertEquals(150000.0, p.budget!!, 0.0)
        assertEquals(13400.0, p.committed, 0.0)
        assertEquals(2, p.decisions)
        assertEquals(1, p.decided)
        assertEquals(4, p.entryCount)
        assertNull(p.access)
        assertTrue(p.isOwner)
        assertTrue(p.canEdit)
        assertFalse(p.archived)
    }

    @Test
    fun parsesItemsAsTheOwner() {
        val items = ProjectsJson.items(ProjectsFixture.text("items_owner"))
        assertEquals(listOf("צלמים", "רעיונות", "DJ"), items.sections.map { it.title })
        val photo = items.sections[0]
        assertTrue(photo.isOptions)
        assertFalse(items.sections[1].isOptions)
        assertEquals(listOf("money", "phone", "checkbox"), photo.columns.map { it.type })
        assertEquals("price", photo.columns[0].role)
        assertEquals("e4e25675", photo.columns[0].key)
        assertEquals("## TL;DR\n- אורי: 13,400", photo.summary)

        assertEquals(4, items.entries.size)
        val chosen = items.entries.first { it.decision == "chosen" }
        assertEquals("אורי עשור", chosen.title)
        assertEquals(photo.id, chosen.sectionId)
        assertEquals(13400L, (chosen.fields[0].value as Number).toLong())
        assertEquals("052-0000000", chosen.fields[1].value)
        val file = chosen.files.single()
        assertEquals("הצעת מחיר.pdf", file.name)
        assertEquals("application/pdf", file.mime)
        assertEquals(14L, file.size)
        assertEquals("הצעת מחיר", file.note)
        assertTrue(file.url.startsWith("/api/entries/${chosen.id}/files/"))
        // A range stays text.
        assertEquals("10500-16500", items.entries.first { it.decision == "rejected" }.fields[0].value)
        assertTrue(items.tasks.isEmpty())
    }

    @Test
    fun aReadShareCarriesItsAccess() {
        val items = ProjectsJson.items(ProjectsFixture.text("items_read"))
        assertEquals("read", items.project.access)
        assertEquals("Sam Example", items.project.sharedBy)
        assertFalse(items.project.canEdit)
        assertFalse(items.project.isOwner)
        assertTrue(items.entries.all { it.access == "read" })
    }

    @Test
    fun writesColumnsAndFields() {
        val items = ProjectsJson.items(ProjectsFixture.text("items_owner"))
        val cols = ApiJson.encodeToJsonElement(ListSerializer(ColumnBody.serializer()), ProjectsJson.columnsBody(items.sections[0].columns)) as JsonArray
        assertEquals(3, cols.size)
        assertEquals("e4e25675", (cols[0] as JsonObject).text("key"))
        assertEquals("price", (cols[0] as JsonObject).text("role"))
        assertFalse((cols[1] as JsonObject).containsKey("role"))

        val fields = ApiJson.encodeToJsonElement(ListSerializer(FieldBody.serializer()), ProjectsJson.fieldsBody(items.entries[0].fields)) as JsonArray
        assertEquals("מחיר כולל", (fields[0] as JsonObject).text("name"))
        assertEquals(13400L, (fields[0] as JsonObject).long("value"))
    }
}
