package io.github.ronganam.dana.signals

import io.github.ronganam.dana.data.signals.DrivingRule
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DrivingRuleTest {
    private fun send(enter: Boolean, bluetoothCar: Boolean = false, bluetoothState: Boolean? = null, lastActivity: Boolean? = null) =
        DrivingRule.send(enter, bluetoothCar, bluetoothState, lastActivity)

    @Test
    fun withoutABluetoothCarARideIsAnEnterThenALeave() {
        assertTrue(send(enter = true))
        assertTrue(send(enter = false, lastActivity = true))
        assertTrue(send(enter = true, lastActivity = false))
    }

    @Test
    fun aStateAlreadySentIsNotSentAgain() {
        assertFalse(send(enter = true, lastActivity = true))
        assertFalse(send(enter = false, lastActivity = false))
    }

    @Test
    fun anExitWithoutAnActivityEnterIsNotALeave() {
        assertFalse(send(enter = false))
        assertFalse(send(enter = false, bluetoothCar = true))
    }

    @Test
    fun theChosenBluetoothCarWinsOnceItsStateIsKnown() {
        for (state in listOf(true, false)) {
            assertFalse(send(enter = true, bluetoothCar = true, bluetoothState = state))
            assertFalse(send(enter = false, bluetoothCar = true, bluetoothState = state, lastActivity = true))
        }
    }

    @Test
    fun aChosenCarThatHasNotConnectedYetLeavesDrivingDetectionOn() {
        assertTrue(send(enter = true, bluetoothCar = true, bluetoothState = null))
        assertTrue(send(enter = false, bluetoothCar = true, bluetoothState = null, lastActivity = true))
    }

    @Test
    fun aBluetoothStateWithoutAChosenCarDoesNotCount() {
        // The device was unchosen: its old state is not the car's any more.
        assertTrue(send(enter = true, bluetoothCar = false, bluetoothState = true))
    }
}
