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
        skyTopColor = 0xFF7395DBL,
        skyBottomColor = 0xFF7395DBL,
        gridLineColor = 0x59FFFFFFL,
        terrainFillTopColor = 0xFF8B7355L,
        terrainFillBottomColor = 0xFF6B5B45L,
        terrainStrokeColor = 0xFF6B5B45L,
        cloudFillColor = 0xFFF5F5FFL,
        cloudStrokeColor = 0xFFDCDCE6L,
        freezingLineColor = 0xFF00BCD4L,
        minus10LineColor = 0xFF2196F3L,
        minus20LineColor = 0xFF1A237EL,
        cruiseLineColor = 0xFF374151L,
        ceilingLineColor = 0xFF9467BDL,
        lclLineColor = 0xFF4CAF50L,
        lfcLineColor = 0xFFFF9800L,
        elLineColor = 0xFFF44336L,
        nightTintColor = 0x470A1432L,
        twilightTintColor = 0x240A1432L,
    )

    val GRAMET = CrossSectionTheme(
        id = "gramet",
        label = "GRAMET",
        skyTopColor = 0xFF2B5DA8L,
        skyBottomColor = 0xFF2B5DA8L,
        gridLineColor = 0x40FFFFFFL,
        terrainFillTopColor = 0xFF8B6914L,
        terrainFillBottomColor = 0xFF6B5010L,
        terrainStrokeColor = 0xFF6B5010L,
        cloudFillColor = 0xFFF5F5FFL,
        cloudStrokeColor = 0xFFDCDCE6L,
        freezingLineColor = 0xFFFF4444L,
        minus10LineColor = 0xFF22CC44L,
        minus20LineColor = 0xFF22CC44L,
        cruiseLineColor = 0xFFE0E0E0L,
        ceilingLineColor = 0xFFCE93D8L,
        lclLineColor = 0xFF00E5FFL,
        lfcLineColor = 0xFFFF9800L,
        elLineColor = 0xFFF44336L,
        nightTintColor = 0x470A1432L,
        twilightTintColor = 0x240A1432L,
    )

    val WINDY = CrossSectionTheme(
        id = "windy",
        label = "Light",
        skyTopColor = 0xFFF8F9FBL,
        skyBottomColor = 0xFFF8F9FBL,
        gridLineColor = 0x2E141E32L,
        terrainFillTopColor = 0xFFA48256L,
        terrainFillBottomColor = 0xFF7A5E3DL,
        terrainStrokeColor = 0xFF7A5E3DL,
        cloudFillColor = 0xFF646E82L,
        cloudStrokeColor = 0xFF46505FL,
        freezingLineColor = 0xFF0277BDL,
        minus10LineColor = 0xFF1565C0L,
        minus20LineColor = 0xFF0D47A1L,
        cruiseLineColor = 0xFF212121L,
        ceilingLineColor = 0xFF6A1B9AL,
        lclLineColor = 0xFF2E7D32L,
        lfcLineColor = 0xFFE65100L,
        elLineColor = 0xFFC62828L,
        nightTintColor = 0x380F172AL,
        twilightTintColor = 0x1F0F172AL,
    )

    val HIGH_CONTRAST = CrossSectionTheme(
        id = "high-contrast",
        label = "High Contrast",
        skyTopColor = 0xFF1B3060L,
        skyBottomColor = 0xFF1B3060L,
        gridLineColor = 0x4DFFFFFFL,
        terrainFillTopColor = 0xFF4A3A28L,
        terrainFillBottomColor = 0xFF362A1DL,
        terrainStrokeColor = 0xFF6B5B45L,
        cloudFillColor = 0xFFE6E6E6L,
        cloudStrokeColor = 0xFFFFFFFFL,
        freezingLineColor = 0xFF00E5FFL,
        minus10LineColor = 0xFF42A5F5L,
        minus20LineColor = 0xFF7C4DFFL,
        cruiseLineColor = 0xFFE0E0E0L,
        ceilingLineColor = 0xFFCE93D8L,
        lclLineColor = 0xFF69F0AEL,
        lfcLineColor = 0xFFFFAB40L,
        elLineColor = 0xFFFF5252L,
        nightTintColor = 0x59000000L,
        twilightTintColor = 0x330F172AL,
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
