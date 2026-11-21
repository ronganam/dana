package io.github.ronganam.dana.projects

import io.github.ronganam.dana.ui.screens.projects.MdBlock
import io.github.ronganam.dana.ui.screens.projects.parseMarkdownBlocks
import org.junit.Assert.assertEquals
import org.junit.Test

/** The small Markdown of section summaries and entry details: headings, lists, tables, code. */
class ProjectMarkdownTest {

    @Test
    fun parsesASummaryWithATable() {
        val blocks = parseMarkdownBlocks(
            """
            ## TL;DR
            - **Garden Hall** fits the budget
              - books fast
            1. Call first

            | | Garden Hall | Sea View |
            |---|---:|---|
            | Price | ₪72,000 | ₪95,000 |
            | Guests | 180 |

            ---
            Book before the end of the month.
            """.trimIndent()
        )
        assertEquals(MdBlock.Heading("TL;DR", 2), blocks[0])
        assertEquals(MdBlock.Item("**Garden Hall** fits the budget", "•", 0), blocks[1])
        assertEquals("books fast", (blocks[2] as MdBlock.Item).text)
        assertEquals(1, (blocks[2] as MdBlock.Item).depth)
        assertEquals("1.", (blocks[3] as MdBlock.Item).marker)
        val table = blocks[4] as MdBlock.Table
        assertEquals(listOf("", "Garden Hall", "Sea View"), table.header)
        assertEquals(listOf(listOf("Price", "₪72,000", "₪95,000"), listOf("Guests", "180", "")), table.rows) // short rows padded
        assertEquals(MdBlock.Rule, blocks[5])
        assertEquals(MdBlock.Paragraph("Book before the end of the month."), blocks[6])
    }

    @Test
    fun checkItemsCodeAndQuotes() {
        val blocks = parseMarkdownBlocks("- [ ] deposit\n- [x] visit\n\n```\nraw | text\n```\n> said so")
        assertEquals(MdBlock.Item("deposit", "☐", 0), blocks[0])
        assertEquals(MdBlock.Item("visit", "☑", 0), blocks[1])
        assertEquals(MdBlock.Code("raw | text"), blocks[2])
        assertEquals(MdBlock.Quote("said so"), blocks[3])
    }

    @Test
    fun aPipeLineWithoutASeparatorIsNotATable() {
        val blocks = parseMarkdownBlocks("a | b\nc")
        assertEquals(1, blocks.size)
        assertEquals("a | b c", (blocks[0] as MdBlock.Paragraph).text.replace("\n", " "))
    }
}
