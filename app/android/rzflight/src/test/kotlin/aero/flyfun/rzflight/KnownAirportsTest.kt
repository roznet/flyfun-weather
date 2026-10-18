package aero.flyfun.rzflight

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.sql.DriverManager

class KnownAirportsTest {

    @Test
    fun loadsSwiftAirportDatabaseSchemaAndFindsAirports() {
        val tempDir = Files.createTempDirectory("airports-swift-schema").toFile()
        try {
            val dbFile = File(tempDir, "airports.db")
            Class.forName("org.sqlite.JDBC")
            DriverManager.getConnection("jdbc:sqlite:${dbFile.absolutePath}").use { conn ->
                conn.createStatement().use { stmt ->
                    stmt.executeUpdate(
                        "CREATE TABLE airports(icao_code TEXT, name TEXT, type TEXT, latitude_deg REAL, longitude_deg REAL, elevation_ft REAL)"
                    )
                    stmt.executeUpdate(
                        "INSERT INTO airports VALUES ('EGTF', 'Fairoaks', 'small_airport', 51.348, -0.559, 80.0)"
                    )
                    stmt.executeUpdate(
                        "INSERT INTO airports VALUES ('LFMD', 'Cannes Mandelieu', 'medium_airport', 43.542, 6.953, 13.0)"
                    )
                }
            }

            val known = KnownAirports(dbFile)
            assertEquals(2, known.size)

            val egtf = known.airport("egtf", ensureRunway = false)
            assertNotNull(egtf)
            assertEquals("EGTF", egtf?.icao)
            assertEquals("Fairoaks", egtf?.name)
            assertEquals(80, egtf?.elevationFt)
            assertEquals(Coord(51.348, -0.559), egtf?.coord)

            assertNull(known.airport("ZZZZ"))
        } finally {
            tempDir.deleteRecursively()
        }
    }

    @Test
    fun rankedSearchOrdersExactIcaoThenPrefixThenNameAndMunicipalitySubstring() {
        val tempDir = Files.createTempDirectory("airports-ranked-search").toFile()
        try {
            val dbFile = File(tempDir, "airports.db")
            Class.forName("org.sqlite.JDBC")
            DriverManager.getConnection("jdbc:sqlite:${dbFile.absolutePath}").use { conn ->
                conn.createStatement().use { stmt ->
                    stmt.executeUpdate(
                        """
                        CREATE TABLE airports(
                            ident TEXT,
                            name TEXT,
                            latitude_deg REAL,
                            longitude_deg REAL,
                            elevation_ft INTEGER,
                            iso_country TEXT,
                            municipality TEXT,
                            type TEXT
                        )
                        """.trimIndent()
                    )
                }
                conn.prepareStatement("INSERT INTO airports VALUES (?, ?, ?, ?, ?, ?, ?, ?)").use { ps ->
                    fun insert(
                        ident: String,
                        name: String,
                        lat: Double,
                        lon: Double,
                        elev: Int,
                        country: String,
                        muni: String,
                        type: String = "small_airport",
                    ) {
                        ps.setString(1, ident)
                        ps.setString(2, name)
                        ps.setDouble(3, lat)
                        ps.setDouble(4, lon)
                        ps.setInt(5, elev)
                        ps.setString(6, country)
                        ps.setString(7, muni)
                        ps.setString(8, type)
                        ps.executeUpdate()
                    }

                    // Name/municipality matches for "EGT" or "Fairoaks"
                    insert("LFPN", "Toussus-le-Noble (Egt Club)", 48.75, 2.11, 538, "FR", "Paris")
                    insert("EGKA", "Shoreham", 50.83, -0.29, 7, "GB", "Brighton Egt")
                    // ICAO prefix matches for "EGT"
                    insert("EGTB", "Wycombe Air Park", 51.61, -0.80, 520, "GB", "High Wycombe")
                    insert("EGTF", "Fairoaks", 51.35, -0.56, 80, "GB", "Chobham")
                    // Exact 3-char identifier to test exact-before-prefix ordering
                    insert("EGT", "Wellington Municipal", 37.32, -97.38, 1277, "US", "Wellington")
                }
            }

            val known = KnownAirports(dbFile)

            // Search "egt" -> 1) exact "EGT", 2) prefix "EGTB", "EGTF", 3) name/municipality "EGKA", "LFPN"
            val results = known.rankedSearch("egt", limit = 12)
            assertEquals(listOf("EGT", "EGTB", "EGTF", "EGKA", "LFPN"), results.map { it.icao })

            // Exact 4-letter ICAO search
            val exactFour = known.rankedSearch("egtf", limit = 5)
            assertEquals("EGTF", exactFour.first().icao)

            // Municipality substring search (case-insensitive)
            val muniResults = known.rankedSearch("chobham", limit = 5)
            assertEquals(listOf("EGTF"), muniResults.map { it.icao })

            // Limit is respected
            val limited = known.rankedSearch("egt", limit = 2)
            assertEquals(listOf("EGT", "EGTB"), limited.map { it.icao })

            // Blank needle returns empty list
            assertEquals(emptyList<Airport>(), known.rankedSearch("   "))
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
