package aero.flyfun.weather.model.viz

data class CrossSectionTheme(
    val id: String,
    val label: String,
    val skyTopColor: Long,
    val skyBottomColor: Long,
    val gridLineColor: Long,
    val terrainFillTopColor: Long,
    val terrainFillBottomColor: Long,
    val terrainStrokeColor: Long,
    val cloudFillColor: Long,
    val cloudStrokeColor: Long,
    val freezingLineColor: Long,
    val minus10LineColor: Long,
    val minus20LineColor: Long,
    val cruiseLineColor: Long,
    val ceilingLineColor: Long,
    val lclLineColor: Long,
    val lfcLineColor: Long,
    val elLineColor: Long,
    val nightTintColor: Long,
    val twilightTintColor: Long,
)

object CrossSectionThemes {
    val STANDARD = CrossSectionTheme(
        id = "standard",
        label = "Standard",
        skyTopColor = 0xFF0A192FL,
        skyBottomColor = 0xFF1A365DL,
        gridLineColor = 0x3394A3B8L,
        terrainFillTopColor = 0xFF78350FL,
        terrainFillBottomColor = 0xFF451A03L,
        terrainStrokeColor = 0xFFB45309L,
        cloudFillColor = 0xFFB0C4DEL,
        cloudStrokeColor = 0xFFCBD5E1L,
        freezingLineColor = 0xFF06B6D4L,
        minus10LineColor = 0xFF3B82F6L,
        minus20LineColor = 0xFF8B5CF6L,
        cruiseLineColor = 0xFFFFFFFFL,
        ceilingLineColor = 0xFFF97316L,
        lclLineColor = 0xFF22C55EL,
        lfcLineColor = 0xFFEAB308L,
        elLineColor = 0xFFA855F7L,
        nightTintColor = 0x590F172AL,
        twilightTintColor = 0x331E293BL,
    )

    val GRAMET = CrossSectionTheme(
        id = "gramet",
        label = "GRAMET",
        skyTopColor = 0xFF2B6CB0L,
        skyBottomColor = 0xFFBEE3F8L,
        gridLineColor = 0x4D475569L,
        terrainFillTopColor = 0xFF8B5A2BL,
        terrainFillBottomColor = 0xFF5C3A21L,
        terrainStrokeColor = 0xFF3E2723L,
        cloudFillColor = 0xFF808080L,
        cloudStrokeColor = 0xFF666666L,
        freezingLineColor = 0xFFE53E3EL,
        minus10LineColor = 0xFFDD6B20L,
        minus20LineColor = 0xFF805AD5L,
        cruiseLineColor = 0xFF1A202CL,
        ceilingLineColor = 0xFFDD6B20L,
        lclLineColor = 0xFF2F855AL,
        lfcLineColor = 0xFFB7791FL,
        elLineColor = 0xFF6B46C1L,
        nightTintColor = 0x400F172AL,
        twilightTintColor = 0x261E293BL,
    )

    val WINDY = CrossSectionTheme(
        id = "windy",
        label = "Windy",
        skyTopColor = 0xFF1E3A5FL,
        skyBottomColor = 0xFF3B6998L,
        gridLineColor = 0x33CBD5E1L,
        terrainFillTopColor = 0xFF5D4037L,
        terrainFillBottomColor = 0xFF3E2723L,
        terrainStrokeColor = 0xFF8D6E63L,
        cloudFillColor = 0xFFE2E8F0L,
        cloudStrokeColor = 0xFFF8FAFCL,
        freezingLineColor = 0xFF22D3EEL,
        minus10LineColor = 0xFF60A5FAL,
        minus20LineColor = 0xFFA78BFAL,
        cruiseLineColor = 0xFFFACC15L,
        ceilingLineColor = 0xFFFB923CL,
        lclLineColor = 0xFF4ADE80L,
        lfcLineColor = 0xFFFACC15L,
        elLineColor = 0xFFC084FCL,
        nightTintColor = 0x590F172AL,
        twilightTintColor = 0x331E293BL,
    )

    val HIGH_CONTRAST = CrossSectionTheme(
        id = "high-contrast",
        label = "High Contrast",
        skyTopColor = 0xFF000000L,
        skyBottomColor = 0xFF0F172AL,
        gridLineColor = 0x6694A3B8L,
        terrainFillTopColor = 0xFF92400EL,
        terrainFillBottomColor = 0xFF451A03L,
        terrainStrokeColor = 0xFFF59E0BL,
        cloudFillColor = 0xFFE2E8F0L,
        cloudStrokeColor = 0xFFFFFFFFL,
        freezingLineColor = 0xFF00FFFFL,
        minus10LineColor = 0xFF38BDF8L,
        minus20LineColor = 0xFFC084FCL,
        cruiseLineColor = 0xFFFFFF00L,
        ceilingLineColor = 0xFFFF6600L,
        lclLineColor = 0xFF00FF66L,
        lfcLineColor = 0xFFFFCC00L,
        elLineColor = 0xFFFF00FFL,
        nightTintColor = 0x66000000L,
        twilightTintColor = 0x380F172AL,
    )

    val ALL: List<CrossSectionTheme> = listOf(STANDARD, GRAMET, WINDY, HIGH_CONTRAST)
    val all: List<CrossSectionTheme> get() = ALL

    fun byId(id: String): CrossSectionTheme = when (id.lowercase()) {
        "standard" -> STANDARD
        "gramet" -> GRAMET
        "windy", "light" -> WINDY
        "high-contrast", "high_contrast" -> HIGH_CONTRAST
        else -> STANDARD
    }
}
