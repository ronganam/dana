package io.github.ronganam.dana.testing

import io.github.ronganam.dana.R
import io.github.ronganam.dana.ui.viewmodels.Texts
import org.w3c.dom.Element
import java.io.File
import java.util.Locale
import javax.xml.parsers.DocumentBuilderFactory

/**
 * [Texts] for JVM tests: the app's English string resources, read from
 * `src/main/res/values/` (the tests run in the module's folder) and formatted
 * the way Android formats them, so tests assert on the words users see.
 */
object ResourceTexts : Texts {
    private val strings = HashMap<String, String>()
    private val plurals = HashMap<String, Map<String, String>>()

    init {
        val dir = File("src/main/res/values")
        val files = dir.listFiles { f -> f.name.endsWith(".xml") }.orEmpty()
        check(files.isNotEmpty()) { "No string resources in ${dir.absolutePath}" }
        for (file in files) {
            val root = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file).documentElement
            val children = root.childNodes
            for (i in 0 until children.length) {
                val node = children.item(i) as? Element ?: continue
                val name = node.getAttribute("name")
                when (node.tagName) {
                    "string" -> strings[name] = unescape(node.textContent)
                    "plurals" -> {
                        val items = node.getElementsByTagName("item")
                        plurals[name] = (0 until items.length).associate {
                            val item = items.item(it) as Element
                            item.getAttribute("quantity") to unescape(item.textContent)
                        }
                    }
                }
            }
        }
    }

    private val stringNames: Map<Int, String> by lazy { names(R.string::class.java) }
    private val pluralNames: Map<Int, String> by lazy { names(R.plurals::class.java) }

    private fun names(type: Class<*>): Map<Int, String> =
        type.fields.associate { it.getInt(null) to it.name }

    private fun unescape(raw: String): String {
        val text = raw.trim().removeSurrounding("\"")
        val out = StringBuilder()
        var i = 0
        while (i < text.length) {
            val c = text[i]
            if (c == '\\' && i + 1 < text.length) {
                out.append(when (val next = text[i + 1]) { 'n' -> '\n'; 't' -> '\t'; else -> next })
                i += 2
            } else {
                out.append(c)
                i++
            }
        }
        return out.toString()
    }

    /** The English text of the string named [name] (no formatting). */
    fun named(name: String): String = strings[name] ?: error("No string named $name")

    override fun get(id: Int, vararg args: Any): String {
        val name = stringNames[id] ?: error("Unknown string id $id")
        val text = named(name)
        return if (args.isEmpty()) text else String.format(Locale.US, text, *args)
    }

    override fun plural(id: Int, count: Int, vararg args: Any): String {
        val name = pluralNames[id] ?: error("Unknown plurals id $id")
        val forms = plurals[name] ?: error("No plurals named $name")
        val text = forms[if (count == 1) "one" else "other"] ?: forms.getValue("other")
        return String.format(Locale.US, text, *args)
    }
}
