package io.github.ronganam.dana.data.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** `GET /api/app/version` as server/core/app_updates.py serves it: camelCase version keys, snake_case notes. */
class UpdateManagerTest {
    private val manifest = """
        {"phone": {"versionName": "9.1.0", "versionCode": 60, "file": "phone-9.1.0.apk", "sha256": "ab12",
                   "size": 1234, "release_notes": "Notes", "updated_at": "2026-01-01T00:00:00Z"},
         "wear": {"versionName": "9.1.0", "versionCode": 60, "file": "wear-9.1.0.apk", "sha256": "cd34", "size": 99}}
    """.trimIndent()

    @Test
    fun readsEveryField() {
        val info = UpdateManager.parseManifest(manifest, "phone")!!
        assertEquals("9.1.0", info.versionName)
        assertEquals(60, info.versionCode)
        assertEquals("phone-9.1.0.apk", info.file)
        assertEquals("ab12", info.sha256)
        assertEquals(1234L, info.size)
        assertEquals("Notes", info.releaseNotes)
        assertEquals(60, UpdateManager.parseManifest(manifest, "wear")!!.versionCode)
    }

    @Test
    fun newerOnlyWithAHigherCodeAndAFile() {
        val info = UpdateManager.parseManifest(manifest, "phone")!!
        assertTrue(UpdateManager.isNewer(info, 59))
        assertFalse(UpdateManager.isNewer(info, 60))
        assertFalse(UpdateManager.isNewer(info.copy(file = ""), 59))
    }

    @Test
    fun missingArtifactIsNull() {
        assertNull(UpdateManager.parseManifest("{}", "phone"))
    }
}
