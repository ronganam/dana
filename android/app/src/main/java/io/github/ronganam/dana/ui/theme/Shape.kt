package io.github.ronganam.dana.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/** Radius scale: 8 controls · 12 inputs/chips · 16 cards · 20 sheets · 28 dialogs. */
val DanaShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(18.dp),
    extraLarge = RoundedCornerShape(28.dp)
)
