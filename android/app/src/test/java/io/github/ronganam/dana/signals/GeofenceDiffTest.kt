package io.github.ronganam.dana.signals

import io.github.ronganam.dana.data.models.ZoneItem
import io.github.ronganam.dana.data.signals.Fence
import io.github.ronganam.dana.data.signals.GeofenceDiff
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GeofenceDiffTest {
    private fun zone(id: String, lat: Double? = 32.0, lon: Double? = 34.8, radius: Double? = 150.0, home: Boolean = false) =
        ZoneItem(name = id, entityId = "zone.$id", id = id, latitude = lat, longitude = lon, radiusM = radius, isHome = home)

    @Test
    fun zonesBecomeFencesHomeFirstWithAMinimumRadius() {
        val fences = GeofenceDiff.fences(listOf(
            zone("gym", radius = 40.0),
            zone("home", home = true),
            zone("nowhere", lat = null),
            zone("", lat = 1.0),
            zone("bad", lat = 95.0)
        ))
        assertEquals(listOf("home", "gym"), fences.map { it.id })
        assertEquals(150f, fences[0].radiusM)
        assertEquals(GeofenceDiff.MIN_RADIUS_M, fences[1].radiusM)
    }

    @Test
    fun atMostAHundredFences() {
        val zones = (1..130).map { zone("z$it") }
        assertEquals(GeofenceDiff.MAX_FENCES, GeofenceDiff.fences(zones).size)
    }

    @Test
    fun addsNewAndMovedRemovesGoneLeavesTheRest() {
        val home = Fence("home", 32.0, 34.8, 150f)
        val gym = Fence("gym", 32.1, 34.9, 100f)
        val office = Fence("office", 32.2, 34.7, 200f)
        val movedGym = gym.copy(latitude = 32.15)
        val cafe = Fence("cafe", 32.3, 34.6, 100f)

        val changes = GeofenceDiff.diff(registered = listOf(home, gym, office), wanted = listOf(home, movedGym, cafe))

        assertEquals(listOf(movedGym, cafe), changes.add)
        assertEquals(listOf("office"), changes.remove)
    }

    @Test
    fun noChangeIsEmpty() {
        val fences = listOf(Fence("home", 32.0, 34.8, 150f), Fence("gym", 32.1, 34.9, 100f))
        assertTrue(GeofenceDiff.diff(fences, fences.reversed()).isEmpty)
    }

    @Test
    fun fromNothingEverythingIsAddedAndToNothingEverythingRemoved() {
        val fences = listOf(Fence("b", 1.0, 1.0, 100f), Fence("a", 2.0, 2.0, 100f))
        assertEquals(fences, GeofenceDiff.diff(emptyList(), fences).add)
        assertEquals(listOf("a", "b"), GeofenceDiff.diff(fences, emptyList()).remove)
    }
}
