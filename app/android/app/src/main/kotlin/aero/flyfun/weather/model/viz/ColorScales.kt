package aero.flyfun.weather.model.viz

import aero.flyfun.weather.model.Assessment
import androidx.compose.ui.graphics.Color
import kotlin.math.max
import kotlin.math.min

object ColorScales {

    val freezingLevelColor: Color = Color(CrossSectionThemes.STANDARD.freezingLineColor)
    val minus10cColor: Color = Color(CrossSectionThemes.STANDARD.minus10LineColor)
    val minus20cColor: Color = Color(CrossSectionThemes.STANDARD.minus20LineColor)
    val lclColor: Color = Color(CrossSectionThemes.STANDARD.lclLineColor)
    val lfcColor: Color = Color(CrossSectionThemes.STANDARD.lfcLineColor)
    val elColor: Color = Color(CrossSectionThemes.STANDARD.elLineColor)
    val cruiseColor: Color = Color(CrossSectionThemes.STANDARD.cruiseLineColor)

    fun icingRiskColorArgb(risk: String, themeId: String = "standard"): Long {
        val r = risk.lowercase()
        return when (themeId.lowercase()) {
            "gramet" -> when (r) {
                "light" -> 0x73AAE6CDL
                "moderate" -> 0x996EC8A5L
                "severe" -> 0xBF2D8264L
                else -> 0x00000000L
            }
            "high-contrast", "high_contrast" -> when (r) {
                "light" -> 0xB3C8DCF0L
                "moderate" -> 0xCC9AB0E0L
                "severe" -> 0xE68470D8L
                else -> 0x00000000L
            }
            "windy", "light" -> when (r) {
                "light" -> 0x8CAA8CDCL
                "moderate" -> 0xB86E50C8L
                "severe" -> 0xE03C1E91L
                else -> 0x00000000L
            }
            else -> when (r) {
                "light" -> 0xB3B9AAE6L
                "moderate" -> 0xD97864D7L
                "severe" -> 0xED41239BL
                else -> 0x00000000L
            }
        }
    }

    fun icingRiskColor(risk: String, themeId: String = "standard"): Color =
        Color(icingRiskColorArgb(risk, themeId))

    fun sfipRiskColorArgb(risk: String, themeId: String = "standard"): Long =
        icingRiskColorArgb(risk, themeId)

    fun sfipRiskColor(risk: String, themeId: String = "standard"): Color =
        Color(sfipRiskColorArgb(risk, themeId))

    fun sldRiskColorArgb(risk: String): Long = when (risk.lowercase()) {
        "light" -> 0x59EC4899L
        "moderate" -> 0x80DB2777L
        "severe" -> 0xA6BE185DL
        else -> 0x4DEC4899L
    }

    fun sldRiskColor(risk: String): Color = Color(sldRiskColorArgb(risk))

    fun catRiskColorArgb(risk: String, themeId: String = "standard"): Long {
        val r = risk.lowercase()
        return when (themeId.lowercase()) {
            "high-contrast", "high_contrast" -> when (r) {
                "light" -> 0x66188848L
                "moderate" -> 0x8C98B830L
                "severe" -> 0xB3C8D010L
                else -> 0x00000000L
            }
            "windy", "light" -> when (r) {
                "light" -> 0x4DFF9800L
                "moderate" -> 0x80F57C00L
                "severe" -> 0xA6C62828L
                else -> 0x00000000L
            }
            else -> when (r) {
                "light" -> 0x33FFC107L
                "moderate" -> 0x66FF9800L
                "severe" -> 0x8CDC3545L
                else -> 0x00000000L
            }
        }
    }

    fun catRiskColor(risk: String, themeId: String = "standard"): Color =
        Color(catRiskColorArgb(risk, themeId))

    fun convectiveRiskColorArgb(risk: String, themeId: String = "standard"): Long {
        val r = risk.lowercase()
        return when (themeId.lowercase()) {
            "high-contrast", "high_contrast" -> when (r) {
                "marginal" -> 0x33F8A020L
                "low" -> 0x47F8A020L
                "moderate" -> 0x61F07820L
                "high" -> 0x7AD85020L
                "extreme" -> 0x8CE81818L
                else -> 0x00000000L
            }
            else -> when (r) {
                "marginal" -> 0x26B4B4B4L
                "low" -> 0x2EFFEB3BL
                "moderate" -> 0x40FF9800L
                "high" -> 0x4DDC3545L
                "extreme" -> 0x59B71C1CL
                else -> 0x00000000L
            }
        }
    }

    fun convectiveRiskColor(risk: String, themeId: String = "standard"): Color =
        Color(convectiveRiskColorArgb(risk, themeId))

    fun coverageToPct(coverage: String): Double = when (coverage.lowercase()) {
        "ovc", "overcast" -> 95.0
        "bkn", "broken" -> 70.0
        "sct", "scattered" -> 40.0
        "few" -> 15.0
        else -> 0.0
    }

    fun cloudCoverageOpacity(coverage: String): Float = when (coverage.lowercase()) {
        "ovc", "overcast" -> 0.82f
        "bkn", "broken" -> 0.68f
        "sct", "scattered" -> 0.52f
        "few" -> 0.25f
        else -> 0.0f
    }

    fun cloudCoverageOpacity(coveragePct: Double): Float {
        if (coveragePct <= 2.0) return 0f
        val clamped = min(100.0, max(0.0, coveragePct))
        return (0.30 + 0.55 * (clamped / 100.0)).toFloat()
    }

    fun cloudCoverageOpacity(coverage: String?, coveragePct: Double?): Float {
        if (coveragePct != null) {
            return cloudCoverageOpacity(coveragePct)
        }
        if (coverage != null) {
            return cloudCoverageOpacity(coverage)
        }
        return 0.45f
    }

    fun cloudFillColor(
        coverage: String = "sct",
        dewpointDepressionC: Double? = null,
        coveragePct: Double? = null,
        theme: CrossSectionTheme = CrossSectionThemes.STANDARD,
    ): Color {
        if (coveragePct != null) {
            // NWP cover%-sourced cloud fill: bright white at low %, darker grey at high % (matching web theme.ts nwpClouds)
            val frac = (coveragePct / 100.0).coerceIn(0.0, 1.0)
            val isLight = theme.id == "windy" || theme.id == "light"
            val r = if (isLight) (225.0 - frac * 155.0) else (245.0 - frac * 105.0)
            val g = if (isLight) (228.0 - frac * 160.0) else (245.0 - frac * 105.0)
            val b = if (isLight) (232.0 - frac * 165.0) else (255.0 - frac * 100.0)
            val floor = if (isLight) 0.35 else 0.30
            val scale = if (isLight) 0.70 else 0.55
            val alpha = (floor + frac * scale).coerceIn(0.05, 0.92).toFloat()
            return Color(
                red = (r / 255.0).toFloat().coerceIn(0f, 1f),
                green = (g / 255.0).toFloat().coerceIn(0f, 1f),
                blue = (b / 255.0).toFloat().coerceIn(0f, 1f),
                alpha = alpha,
            )
        }
        // DD-sourced cloud fill: dense (low DD) -> grey, thin (high DD) -> white
        val baseOpacity = cloudCoverageOpacity(coverage, null)
        val ddFrac = ((dewpointDepressionC ?: 1.5) / 3.0).coerceIn(0.0, 1.0)
        val isLight = theme.id == "windy" || theme.id == "light"
        val r = if (isLight) (70.0 + ddFrac * 125.0) else (140.0 + ddFrac * 110.0)
        val g = if (isLight) (80.0 + ddFrac * 120.0) else (140.0 + ddFrac * 110.0)
        val b = if (isLight) (95.0 + ddFrac * 115.0) else (150.0 + ddFrac * 105.0)
        val alpha = min(0.92f, max(0.10f, baseOpacity * (1.0f - 0.25f * ddFrac.toFloat())))
        return Color(
            red = (r / 255.0).toFloat().coerceIn(0f, 1f),
            green = (g / 255.0).toFloat().coerceIn(0f, 1f),
            blue = (b / 255.0).toFloat().coerceIn(0f, 1f),
            alpha = alpha,
        )
    }

    fun inversionOpacity(strengthC: Double): Float {
        val clamped = min(3.0, max(0.0, strengthC))
        return min(0.65, 0.15 + (clamped / 3.0) * 0.50).toFloat()
    }

    fun inversionColor(strengthC: Double, themeId: String = "standard"): Color {
        val alpha = inversionOpacity(strengthC)
        val base = when (themeId.lowercase()) {
            "high-contrast", "high_contrast" -> Color(0xFFFF5252L)
            "windy", "light" -> Color(0xFFC2185BL)
            else -> Color(0xFFE91E63L)
        }
        return base.copy(alpha = alpha)
    }

    fun flightCategoryColorArgb(category: String?): Long = when (category?.uppercase()) {
        "VFR" -> 0xFF22C55EL
        "MVFR" -> 0xFF3B82F6L
        "IFR" -> 0xFFEF4444L
        "LIFR" -> 0xFFD946EFL
        else -> 0xFF94A3B8L
    }

    fun flightCategoryColor(category: String?): Color =
        Color(flightCategoryColorArgb(category))

    fun assessmentColorArgb(status: String?): Long = when (status?.uppercase()) {
        "GREEN" -> 0xFF22C55EL
        "AMBER" -> 0xFFF59E0BL
        "RED" -> 0xFFEF4444L
        else -> 0xFF94A3B8L
    }

    fun assessmentColor(status: String?): Color =
        Color(assessmentColorArgb(status))

    fun assessmentColor(assessment: Assessment): Color = when (assessment) {
        Assessment.GREEN -> Color(0xFF22C55EL)
        Assessment.AMBER -> Color(0xFFF59E0BL)
        Assessment.RED -> Color(0xFFEF4444L)
        Assessment.UNAVAILABLE -> Color(0xFF94A3B8L)
    }

    fun frontTypeColor(frontType: String): Color = when (frontType.lowercase()) {
        "cold" -> Color(0xFF3B82F6L)
        "warm" -> Color(0xFFEF4444L)
        "occluded" -> Color(0xFFA855F7L)
        "stationary" -> Color(0xFFF59E0BL)
        else -> Color(0xFF94A3B8L)
    }
}
