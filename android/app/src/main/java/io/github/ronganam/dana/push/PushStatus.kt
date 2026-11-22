package io.github.ronganam.dana.push

/** How the server wakes this app: the `transport` of `POST /api/phone/register`. */
enum class PushTransport(val wire: String, val label: String) {
    /** Firebase Cloud Messaging: only in a build with your own Firebase project (google-services.json). */
    FCM("fcm", "Firebase (FCM)"),

    /** UnifiedPush through a distributor app on the phone (ntfy). */
    UNIFIEDPUSH("unifiedpush", "UnifiedPush");

    companion object {
        fun of(wire: String?): PushTransport? = entries.firstOrNull { it.wire == wire }
    }
}

/** What Settings → Push shows (`PushSetup.status`). */
data class PushStatus(
    val transport: PushTransport,
    /** UnifiedPush: the distributor app's name; null when none is installed (and for FCM). */
    val distributor: String?,
    /** The server has this phone's current registration. */
    val registered: Boolean,
    /** When the last ping came (epoch ms); 0 = never. */
    val lastPingAt: Long,
    /** Why push isn't working, in words for the user; null when nothing is wrong. */
    val problem: String?,
)

/** UnifiedPush distributors: which one Dana uses, and where to get ntfy. */
object Distributors {
    /** The ntfy app (public ntfy.sh by default, or your own ntfy server). */
    const val NTFY = "io.heckel.ntfy"
    const val NTFY_STORE = "market://details?id=$NTFY"
    const val NTFY_FDROID = "https://f-droid.org/packages/$NTFY/"

    /**
     * The distributor to use among the [installed] ones (package names; this
     * app's own, [self], is never one): the one already chosen while it is
     * installed, else ntfy, else the first; null when there is none.
     */
    fun pick(installed: List<String>, saved: String?, self: String): String? {
        val others = installed.filter { it != self }
        return saved?.takeIf { it in others } ?: others.firstOrNull { it == NTFY } ?: others.firstOrNull()
    }
}
