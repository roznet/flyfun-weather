package aero.flyfun.weather.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

private val CockpitDarkColorScheme = darkColorScheme(
    primary = CockpitDarkPrimary,
    onPrimary = CockpitDarkOnPrimary,
    primaryContainer = CockpitDarkPrimaryContainer,
    onPrimaryContainer = CockpitDarkOnPrimaryContainer,
    secondary = CockpitDarkSecondary,
    onSecondary = CockpitDarkOnSecondary,
    secondaryContainer = CockpitDarkSecondaryContainer,
    onSecondaryContainer = CockpitDarkOnSecondaryContainer,
    tertiary = CockpitDarkTertiary,
    onTertiary = CockpitDarkOnTertiary,
    tertiaryContainer = CockpitDarkTertiaryContainer,
    onTertiaryContainer = CockpitDarkOnTertiaryContainer,
    background = CockpitDarkBackground,
    onBackground = CockpitDarkOnBackground,
    surface = CockpitDarkSurface,
    onSurface = CockpitDarkOnSurface,
    surfaceVariant = CockpitDarkSurfaceVariant,
    onSurfaceVariant = CockpitDarkOnSurfaceVariant,
    outline = CockpitDarkOutline,
    error = AssessmentRed,
)

private val CockpitLightColorScheme = lightColorScheme(
    primary = CockpitLightPrimary,
    onPrimary = CockpitLightOnPrimary,
    primaryContainer = CockpitLightPrimaryContainer,
    onPrimaryContainer = CockpitLightOnPrimaryContainer,
    secondary = CockpitLightSecondary,
    onSecondary = CockpitLightOnSecondary,
    secondaryContainer = CockpitLightSecondaryContainer,
    onSecondaryContainer = CockpitLightOnSecondaryContainer,
    tertiary = CockpitLightTertiary,
    onTertiary = CockpitLightOnTertiary,
    tertiaryContainer = CockpitLightTertiaryContainer,
    onTertiaryContainer = CockpitLightOnTertiaryContainer,
    background = CockpitLightBackground,
    onBackground = CockpitLightOnBackground,
    surface = CockpitLightSurface,
    onSurface = CockpitLightOnSurface,
    surfaceVariant = CockpitLightSurfaceVariant,
    onSurfaceVariant = CockpitLightOnSurfaceVariant,
    outline = CockpitLightOutline,
    error = AssessmentRed,
)

val CockpitTypography = Typography(
    headlineMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Bold,
        fontSize = 24.sp,
        lineHeight = 30.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Bold,
        fontSize = 20.sp,
        lineHeight = 26.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    titleSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
)

/**
 * Cockpit-safe Material 3 theme for FlyFun Weather.
 * Explicitly avoids Android dynamic wallpaper colors so aviation semantic colors
 * (Green / Amber / Red assessments and VFR / MVFR / IFR / LIFR categories) stay unambiguous.
 */
@Composable
fun FlyFunWeatherTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colorScheme = if (darkTheme) CockpitDarkColorScheme else CockpitLightColorScheme
    MaterialTheme(
        colorScheme = colorScheme,
        typography = CockpitTypography,
        content = content,
    )
}
