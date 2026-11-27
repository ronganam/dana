package io.github.ronganam.dana.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.DeviceFontFamilyName
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.googlefonts.Font
import androidx.compose.ui.text.googlefonts.GoogleFont
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.R

/*
 * One face (Circuit): Roboto Flex, a Google Fonts downloadable font fetched
 * through Play services (certificates in res/values/font_certs.xml), with the
 * system sans as the fallback (no Play services, offline before the first
 * download, previews). Hebrew falls through to the system's Hebrew face.
 * Sizes: display 34–40 for screen titles, title 22, body 16, supporting 14,
 * 12 only for axis and time labels. Times use tabular figures ([tabular]).
 */

private val GoogleFonts = GoogleFont.Provider(
    providerAuthority = "com.google.android.gms.fonts",
    providerPackage = "com.google.android.gms",
    certificates = R.array.com_google_android_gms_fonts_certs
)

private val RobotoFlex = GoogleFont("Roboto Flex")

private val Weights = listOf(FontWeight.Normal, FontWeight.Medium, FontWeight.SemiBold, FontWeight.Bold)

/** Roboto Flex, then the system sans at the same weight. */
val DanaSans = FontFamily(
    Weights.map { Font(googleFont = RobotoFlex, fontProvider = GoogleFonts, weight = it) } +
        Weights.map { Font(DeviceFontFamilyName("sans-serif"), weight = it) }
)

/**
 * The editorial serif of the previous design. Not in any of [DanaTypography]'s
 * styles; only the assistant sheet's "d" mark (the launcher icon's letter), the
 * TV's clock and the kiosk's pairing title still use it.
 */
val DanaSerif = FontFamily(
    Font(R.font.instrument_serif_regular, FontWeight.Normal),
    Font(R.font.instrument_serif_italic, FontWeight.Normal, androidx.compose.ui.text.font.FontStyle.Italic)
)

/** Monospace for code only (code spans, tool arguments, pairing codes); never for labels. */
val DmMono = FontFamily(
    Font(R.font.dm_mono_regular, FontWeight.Normal)
)

/** Tabular figures, so times and counts line up and don't jitter as they change. */
fun TextStyle.tabular(): TextStyle = copy(fontFeatureSettings = "tnum")

val DanaTypography = Typography(
    // Screen titles ("Watching", "Good morning") and big values.
    displayLarge = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 40.sp, lineHeight = 48.sp, letterSpacing = (-0.5).sp),
    displayMedium = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 36.sp, lineHeight = 44.sp, letterSpacing = (-0.5).sp),
    displaySmall = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 34.sp, lineHeight = 40.sp, letterSpacing = (-0.25).sp),
    headlineLarge = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 28.sp, lineHeight = 36.sp, letterSpacing = (-0.2).sp),
    headlineMedium = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 24.sp, lineHeight = 32.sp, letterSpacing = (-0.1).sp),
    headlineSmall = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 22.sp, lineHeight = 28.sp, letterSpacing = 0.sp),
    // Title 22 (a card's or sheet's heading), row titles 16, small titles 14.
    titleLarge = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.SemiBold, fontSize = 22.sp, lineHeight = 28.sp, letterSpacing = 0.sp),
    titleMedium = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 16.sp, lineHeight = 22.sp, letterSpacing = 0.sp),
    titleSmall = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 14.sp, lineHeight = 20.sp, letterSpacing = 0.sp),
    // Body 16, supporting 14, 12 for axis and time labels.
    bodyLarge = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Normal, fontSize = 16.sp, lineHeight = 24.sp, letterSpacing = 0.sp),
    bodyMedium = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Normal, fontSize = 14.sp, lineHeight = 20.sp, letterSpacing = 0.sp),
    bodySmall = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Normal, fontSize = 12.sp, lineHeight = 16.sp, letterSpacing = 0.sp),
    // Buttons; metadata lines (times, counts: tabular).
    labelLarge = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, lineHeight = 20.sp, letterSpacing = 0.sp),
    labelMedium = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp, letterSpacing = 0.sp, fontFeatureSettings = "tnum"),
    labelSmall = TextStyle(fontFamily = DanaSans, fontWeight = FontWeight.Normal, fontSize = 12.sp, lineHeight = 16.sp, letterSpacing = 0.sp, fontFeatureSettings = "tnum")
)
