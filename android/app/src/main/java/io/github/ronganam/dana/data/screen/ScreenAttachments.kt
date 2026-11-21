package io.github.ronganam.dana.data.screen

/** The screen going out with one message: `device_screen` and the screenshot as JPEG. */
class OutgoingScreen(
    val screen: DeviceScreen?,
    val jpeg: ByteArray?,
    /** For the caption under the sent message ("Screen · Chrome"). */
    val label: String
)

/**
 * Where `ChatController` gets the screen for the message it is sending (set
 * only by the power-button sheet; the app, kiosk and TV have none).
 */
interface ScreenAttachments {
    /** The screen to send with this message if it was included; the control turns off. */
    fun take(): OutgoingScreen?

    /** A send the server refused (thread busy): include it with the next message again. */
    fun putBack(screen: OutgoingScreen)
}
