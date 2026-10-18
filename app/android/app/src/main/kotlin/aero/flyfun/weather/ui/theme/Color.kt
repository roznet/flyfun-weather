package aero.flyfun.weather.ui.theme

import androidx.compose.ui.graphics.Color
import aero.flyfun.weather.model.Assessment

// ============================================================================
// Cockpit-safe Assessment Semantic Colors
// ============================================================================

val AssessmentGreen = Color(0xFF16A34A)
val AssessmentAmber = Color(0xFFD97706)
val AssessmentRed = Color(0xFFDC2626)
val AssessmentUnavailable = Color(0xFF6B7280)

val AssessmentGreenContainerDark = Color(0xFF052E16)
val AssessmentAmberContainerDark = Color(0xFF451A03)
val AssessmentRedContainerDark = Color(0xFF450A0A)
val AssessmentUnavailableContainerDark = Color(0xFF1F2937)

val AssessmentGreenContainerLight = Color(0xFFDCFCE7)
val AssessmentAmberContainerLight = Color(0xFFFEF3C7)
val AssessmentRedContainerLight = Color(0xFFFEE2E2)
val AssessmentUnavailableContainerLight = Color(0xFFF3F4F6)

fun Assessment.color(): Color = when (this) {
    Assessment.GREEN -> AssessmentGreen
    Assessment.AMBER -> AssessmentAmber
    Assessment.RED -> AssessmentRed
    Assessment.UNAVAILABLE -> AssessmentUnavailable
}

val Assessment.composeColor: Color
    get() = color()

fun Assessment.containerColor(darkTheme: Boolean = false): Color = when (this) {
    Assessment.GREEN -> if (darkTheme) AssessmentGreenContainerDark else AssessmentGreenContainerLight
    Assessment.AMBER -> if (darkTheme) AssessmentAmberContainerDark else AssessmentAmberContainerLight
    Assessment.RED -> if (darkTheme) AssessmentRedContainerDark else AssessmentRedContainerLight
    Assessment.UNAVAILABLE -> if (darkTheme) AssessmentUnavailableContainerDark else AssessmentUnavailableContainerLight
}

fun assessmentColor(wireStatus: String?): Color =
    Assessment.fromString(wireStatus).color()

// ============================================================================
// Aviation Flight Category Semantic Colors (VFR / MVFR / IFR / LIFR)
// ============================================================================

val CategoryVfr = Color(0xFF16A34A)
val CategoryMvfr = Color(0xFF2563EB)
val CategoryIfr = Color(0xFFDC2626)
val CategoryLifr = Color(0xFFD946EF)
val CategoryUnknown = Color(0xFF6B7280)

fun flightCategoryColor(category: String?): Color = when (category?.trim()?.uppercase()) {
    "VFR" -> CategoryVfr
    "MVFR" -> CategoryMvfr
    "IFR" -> CategoryIfr
    "LIFR" -> CategoryLifr
    else -> CategoryUnknown
}

// ============================================================================
// Cockpit High-Contrast Material 3 Palette
// ============================================================================

// Dark Cockpit (night operations — deep slate background, crisp high-contrast text)
val CockpitDarkPrimary = Color(0xFF38BDF8)
val CockpitDarkOnPrimary = Color(0xFF082F49)
val CockpitDarkPrimaryContainer = Color(0xFF0C4A6E)
val CockpitDarkOnPrimaryContainer = Color(0xFFE0F2FE)

val CockpitDarkSecondary = Color(0xFF94A3B8)
val CockpitDarkOnSecondary = Color(0xFF0F172A)
val CockpitDarkSecondaryContainer = Color(0xFF1E293B)
val CockpitDarkOnSecondaryContainer = Color(0xFFE2E8F0)

val CockpitDarkTertiary = Color(0xFF818CF8)
val CockpitDarkOnTertiary = Color(0xFF1E1B4B)
val CockpitDarkTertiaryContainer = Color(0xFF312E81)
val CockpitDarkOnTertiaryContainer = Color(0xFFE0E7FF)

val CockpitDarkBackground = Color(0xFF0B0F19)
val CockpitDarkOnBackground = Color(0xFFF8FAFC)
val CockpitDarkSurface = Color(0xFF111827)
val CockpitDarkOnSurface = Color(0xFFF8FAFC)
val CockpitDarkSurfaceVariant = Color(0xFF1E293B)
val CockpitDarkOnSurfaceVariant = Color(0xFFCBD5E1)
val CockpitDarkOutline = Color(0xFF475569)

// Bright Cockpit (day VFR — sunlight-readable high contrast)
val CockpitLightPrimary = Color(0xFF0284C7)
val CockpitLightOnPrimary = Color(0xFFFFFFFF)
val CockpitLightPrimaryContainer = Color(0xFFE0F2FE)
val CockpitLightOnPrimaryContainer = Color(0xFF0C4A6E)

val CockpitLightSecondary = Color(0xFF334155)
val CockpitLightOnSecondary = Color(0xFFFFFFFF)
val CockpitLightSecondaryContainer = Color(0xFFE2E8F0)
val CockpitLightOnSecondaryContainer = Color(0xFF0F172A)

val CockpitLightTertiary = Color(0xFF4F46E5)
val CockpitLightOnTertiary = Color(0xFFFFFFFF)
val CockpitLightTertiaryContainer = Color(0xFFE0E7FF)
val CockpitLightOnTertiaryContainer = Color(0xFF1E1B4B)

val CockpitLightBackground = Color(0xFFF8FAFC)
val CockpitLightOnBackground = Color(0xFF0F172A)
val CockpitLightSurface = Color(0xFFFFFFFF)
val CockpitLightOnSurface = Color(0xFF0F172A)
val CockpitLightSurfaceVariant = Color(0xFFF1F5F9)
val CockpitLightOnSurfaceVariant = Color(0xFF334155)
val CockpitLightOutline = Color(0xFF94A3B8)
