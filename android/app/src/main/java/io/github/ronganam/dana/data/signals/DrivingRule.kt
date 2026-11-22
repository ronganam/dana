package io.github.ronganam.dana.data.signals

/**
 * When an Activity Recognition transition (in a vehicle: enter / exit) becomes
 * a car event (`via: activity`). Bluetooth wins: once the car chosen in "This
 * is my car" has connected or disconnected since it was chosen, its connection
 * alone says enter / leave and driving detection stays quiet (choosing "No
 * device", or another device, makes the car's state unknown again). Otherwise
 * an enter goes unless the last activity event sent was already an enter, and
 * an exit goes only after an activity enter, so a ride whose enter Bluetooth
 * took over never ends with a stray leave.
 */
object DrivingRule {
    /**
     * Whether a transition ([enter]) is sent. [bluetoothCar]: a car device is
     * chosen; [bluetoothState]: what its connection last said (null: nothing
     * since it was chosen); [lastActivity]: the last car state sent from
     * activity (null: none, or Bluetooth took over).
     */
    fun send(enter: Boolean, bluetoothCar: Boolean, bluetoothState: Boolean?, lastActivity: Boolean?): Boolean = when {
        bluetoothCar && bluetoothState != null -> false
        enter -> lastActivity != true
        else -> lastActivity == true
    }
}
