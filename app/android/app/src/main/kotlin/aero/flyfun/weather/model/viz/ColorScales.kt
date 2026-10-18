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
                "light" -> 0x5900B4D8L
                "moderate" -> 0x800077B6L
                "severe" -> 0xA603045EL
                else -> 0x00000000L
            }
            "high-contrast", "high_contrast" -> when (r) {
                "light" -> 0x7338BDF8L
                "moderate" -> 0x993B82F6L
                "severe" -> 0xCCEF4444L
                else -> 0x00000000L
            }
            else -> when (r) {
                "light" -> 0x5963B3EDL
                "moderate" -> 0x733182CEL
                "severe" -> 0x99E53E3EL
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
                "light" -> 0x66FBBF24L
                "moderate" -> 0x8CF97316L
                "severe" -> 0xB3DC2626L
                else -> 0x00000000L
            }
            else -> when (r) {
                "light" -> 0x40F6AD55L
                "moderate" -> 0x66DD6B20L
                "severe" -> 0x8CC53030L
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
                "marginal", "low" -> 0x59FACC15L
                "moderate" -> 0x80F97316L
                "high" -> 0xA6EF4444L
                "extreme" -> 0xCCDC2626L
                else -> 0x00000000L
            }
            else -> when (r) {
                "marginal", "low" -> 0x40ECC94BL
                "moderate" -> 0x59ED8936L
                "high" -> 0x73E53E3EL
                "extreme" -> 0x999B2C2CL
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
        "ovc", "overcast" -> 0.78f
        "bkn", "broken" -> 0.56f
        "sct", "scattered" -> 0.34f
        "few" -> 0.18f
        else -> 0.0f
    }

    fun cloudCoverageOpacity(coveragePct: Double): Float {
        if (coveragePct <= 2.0) return 0f
        val clamped = min(100.0, max(0.0, coveragePct))
        return (0.12 + 0.72 * (clamped / 100.0)).toFloat()
    }

    fun cloudCoverageOpacity(coverage: String?, coveragePct: Double?): Float {
        if (coveragePct != null) {
            return cloudCoverageOpacity(coveragePct)
        }
        if (coverage != null) {
            return cloudCoverageOpacity(coverage)
        }
        return 0.34f
    }

    fun cloudFillColor(
        coverage: String = "sct",
        dewpointDepressionC: Double? = null,
        coveragePct: Double? = null,
        theme: CrossSectionTheme = CrossSectionThemes.STANDARD,
    ): Color {
        val baseOpacity = cloudCoverageOpacity(coverage, coveragePct)
        val ddFactor = if (dewpointDepressionC != null) {
            val clampedDd = min(4.0, max(0.0, dewpointDepressionC))
            (1.0 - 0.15 * clampedDd).toFloat()
        } else {
            1.0f
        }
        val alpha = min(0.9f, max(0.05f, baseOpacity * ddFactor))
        val base = Color(theme.cloudFillColor)
        return base.copy(alpha = alpha)
    }

    fun inversionOpacity(strengthC: Double): Float {
        val clamped = min(8.0, max(0.5, strengthC))
        return (0.12 + 0.38 * ((clamped - 0.5) / 7.5)).toFloat()
    }

    fun inversionColor(strengthC: Double, themeId: String = "standard"): Color {
        val alpha = inversionOpacity(strengthC)
        val base = when (themeId.lowercase()) {
            "high-contrast", "high_contrast" -> Color(0xFFF472B6L)
            else -> Color(0xFFD53F8CL)
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
