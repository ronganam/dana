package io.github.ronganam.dana.data.signals

import io.github.ronganam.dana.data.models.ZoneItem

/** One geofence the phone keeps: a zone's id (the request id), centre and radius. */
data class Fence(val id: String, val latitude: Double, val longitude: Double, val radiusM: Float)

/** What to change: fences to [add] (new or moved; adding an id again replaces it) and ids to [remove]. */
data class FenceChanges(val add: List<Fence>, val remove: List<String>) {
    val isEmpty: Boolean get() = add.isEmpty() && remove.isEmpty()
}

/** The user's zones → the phone's geofences, and what changes when the zones do. */
object GeofenceDiff {
    /** Android keeps at most 100 geofences per app. */
    const val MAX_FENCES = 100
    /** Smaller circles fire late or not at all (Android's advice: 100–150 m at least). */
    const val MIN_RADIUS_M = 100f

    /** The fences for [zones]: those with an id and a place, the radius raised to [MIN_RADIUS_M], Home first, at most [MAX_FENCES]. */
    fun fences(zones: List<ZoneItem>): List<Fence> = zones
        .sortedByDescending { it.isHome }
        .mapNotNull { z ->
            val lat = z.latitude ?: return@mapNotNull null
            val lon = z.longitude ?: return@mapNotNull null
            if (z.id.isEmpty() || lat !in -90.0..90.0 || lon !in -180.0..180.0) null
            else Fence(z.id, lat, lon, maxOf((z.radiusM ?: 0.0).toFloat(), MIN_RADIUS_M))
        }
        .distinctBy { it.id }
        .take(MAX_FENCES)

    /** What turns [registered] into [wanted]: unchanged fences are left alone (no initial events, no work). */
    fun diff(registered: Collection<Fence>, wanted: List<Fence>): FenceChanges {
        val have = registered.associateBy { it.id }
        val want = wanted.associateBy { it.id }
        return FenceChanges(
            add = wanted.filter { have[it.id] != it },
            remove = have.keys.filter { it !in want }.sorted()
        )
    }
}
