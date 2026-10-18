package aero.flyfun.rzflight

import java.io.File
import java.sql.DriverManager
import java.sql.ResultSet
import kotlin.math.roundToInt

/**
 * Pluggable source of [Airport] rows so [KnownAirports] can be backed by a SQLite file
 * via JDBC in JVM tests, Android's `SQLiteDatabase`, or an in-memory fixture list.
 */
fun interface AirportDataSource {
    fun loadAirports(): List<Airport>
}

/**
 * Reads the `airports` table from a SQLite file on disk.
 *
 * Supports both schema variants used across FlyFun / euro_aip / OurAirports:
 * - `icao_code` or `ident` for the ICAO identifier
 * - `name`, `latitude_deg`, `longitude_deg`, `elevation_ft`
 * - optional `iso_country`, `municipality`, `type`
 *
 * Uses JDBC (`jdbc:sqlite:...`) when available on the JVM, with a reflective fallback
 * to `android.database.sqlite.SQLiteDatabase` when running on the Android runtime without JDBC.
 */
class SqliteFileAirportDataSource(private val dbFile: File) : AirportDataSource {

    override fun loadAirports(): List<Airport> {
        if (!dbFile.exists() || !dbFile.isFile) return emptyList()
        return runCatching { loadViaJdbc() }
            .recoverCatching { loadViaAndroidSqlite() }
            .getOrDefault(emptyList())
    }

    private fun loadViaJdbc(): List<Airport> {
        runCatching { Class.forName("org.sqlite.JDBC") }
        val url = "jdbc:sqlite:${dbFile.absolutePath}"
        val airports = mutableListOf<Airport>()

        DriverManager.getConnection(url).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("SELECT * FROM airports").use { rs ->
                    val columns = columnNameSet(rs)
                    while (rs.next()) {
                        val icao = firstNonBlank(rs, columns, "icao_code", "ident", "icao")
                            ?.trim()
                            ?.uppercase()
                        if (icao.isNullOrEmpty()) continue

                        val name = getStringOrEmpty(rs, columns, "name")
                        val lat = getDoubleOrZero(rs, columns, "latitude_deg")
                        val lon = getDoubleOrZero(rs, columns, "longitude_deg")
                        val elev = getDoubleOrZero(rs, columns, "elevation_ft").roundToInt()
                        val isoCountry = getStringOrEmpty(rs, columns, "iso_country")
                        val municipality = getStringOrEmpty(rs, columns, "municipality")
                        val type = getStringOrEmpty(rs, columns, "type")

                        airports.add(
                            Airport(
                                icao = icao,
                                name = name,
                                latitude = lat,
                                longitude = lon,
                                elevationFt = elev,
                                isoCountry = isoCountry,
                                municipality = municipality,
                                type = type,
                            )
                        )
                    }
                }
            }
        }
        return airports
    }

    private fun loadViaAndroidSqlite(): List<Airport> {
        val sqliteClass = Class.forName("android.database.sqlite.SQLiteDatabase")
        val openMethod = sqliteClass.getMethod(
            "openDatabase",
            String::class.java,
            Class.forName("android.database.sqlite.SQLiteDatabase\$CursorFactory"),
            Int::class.javaPrimitiveType,
        )
        val openReadonly = sqliteClass.getField("OPEN_READONLY").getInt(null)
        val db = openMethod.invoke(null, dbFile.absolutePath, null, openReadonly) as AutoCloseable
        val airports = mutableListOf<Airport>()

        db.use {
            val rawQuery = sqliteClass.getMethod(
                "rawQuery",
                String::class.java,
                Array<String>::class.java,
            )
            val cursor = rawQuery.invoke(db, "SELECT * FROM airports", null) as AutoCloseable
            cursor.use {
                val cursorClass = cursor.javaClass
                val getColumnNames = cursorClass.getMethod("getColumnNames")
                val moveToNext = cursorClass.getMethod("moveToNext")
                val getColumnIndex = cursorClass.getMethod("getColumnIndex", String::class.java)
                val isNull = cursorClass.getMethod("isNull", Int::class.javaPrimitiveType)
                val getString = cursorClass.getMethod("getString", Int::class.javaPrimitiveType)
                val getDouble = cursorClass.getMethod("getDouble", Int::class.javaPrimitiveType)

                @Suppress("UNCHECKED_CAST")
                val colNames = (getColumnNames.invoke(cursor) as Array<String>)
                    .associateBy({ it.lowercase() }, { getColumnIndex.invoke(cursor, it) as Int })

                fun readString(col: String): String {
                    val idx = colNames[col] ?: return ""
                    if (idx < 0 || (isNull.invoke(cursor, idx) as Boolean)) return ""
                    return (getString.invoke(cursor, idx) as? String)?.trim().orEmpty()
                }

                fun readDouble(col: String): Double {
                    val idx = colNames[col] ?: return 0.0
                    if (idx < 0 || (isNull.invoke(cursor, idx) as Boolean)) return 0.0
                    return (getDouble.invoke(cursor, idx) as? Number)?.toDouble() ?: 0.0
                }

                while (moveToNext.invoke(cursor) as Boolean) {
                    val icao = sequenceOf("icao_code", "ident", "icao")
                        .map { readString(it) }
                        .firstOrNull { it.isNotEmpty() }
                        ?.uppercase()
                    if (icao.isNullOrEmpty()) continue

                    airports.add(
                        Airport(
                            icao = icao,
                            name = readString("name"),
                            latitude = readDouble("latitude_deg"),
                            longitude = readDouble("longitude_deg"),
                            elevationFt = readDouble("elevation_ft").roundToInt(),
                            isoCountry = readString("iso_country"),
                            municipality = readString("municipality"),
                            type = readString("type"),
                        )
                    )
                }
            }
        }
        return airports
    }

    private fun columnNameSet(rs: ResultSet): Set<String> {
        val meta = rs.metaData
        val set = mutableSetOf<String>()
        for (i in 1..meta.columnCount) {
            set.add(meta.getColumnName(i).lowercase())
        }
        return set
    }

    private fun firstNonBlank(rs: ResultSet, columns: Set<String>, vararg candidates: String): String? {
        for (col in candidates) {
            if (col.lowercase() in columns) {
                val value = rs.getString(col)?.trim()
                if (!value.isNullOrEmpty()) return value
            }
        }
        return null
    }

    private fun getStringOrEmpty(rs: ResultSet, columns: Set<String>, column: String): String {
        if (column.lowercase() !in columns) return ""
        return rs.getString(column)?.trim().orEmpty()
    }

    private fun getDoubleOrZero(rs: ResultSet, columns: Set<String>, column: String): Double {
        if (column.lowercase() !in columns) return 0.0
        val v = rs.getDouble(column)
        return if (rs.wasNull()) 0.0 else v
    }
}

/**
 * In-memory airport index with tiered ICAO / name / municipality search, mirroring
 * `RZFlight.KnownAirports`.
 */
class KnownAirports(airports: List<Airport>) {

    constructor(dataSource: AirportDataSource) : this(dataSource.loadAirports())

    constructor(dbFile: File) : this(SqliteFileAirportDataSource(dbFile))

    private val airportsByIcao: Map<String, Airport>
    private val sortedAirports: List<Airport>

    init {
        val map = LinkedHashMap<String, Airport>(airports.size)
        for (airport in airports) {
            val normalizedIcao = airport.icao.trim().uppercase()
            if (normalizedIcao.isNotEmpty()) {
                map[normalizedIcao] = airport.copy(icao = normalizedIcao)
            }
        }
        sortedAirports = map.values.sortedBy { it.icao }
        airportsByIcao = map
    }

    val size: Int
        get() = sortedAirports.size

    fun all(): List<Airport> = sortedAirports

    /**
     * Look up a single airport by ICAO code (case-insensitive).
     * [ensureRunway] is accepted for API parity with iOS `KnownAirports.airport(icao:ensureRunway:)`.
     */
    fun airport(icao: String, ensureRunway: Boolean = false): Airport? {
        val key = icao.trim().uppercase()
        if (key.isEmpty()) return null
        return airportsByIcao[key]
    }

    /**
     * Tiered case-insensitive search across known airports:
     * 1. Exact ICAO match (`airport.icao == needle`)
     * 2. ICAO prefix match (`airport.icao.startsWith(needle)`)
     * 3. Airport name or municipality substring match (`name` or `municipality` contains `needle`)
     */
    fun rankedSearch(needle: String, limit: Int = 12): List<Airport> {
        val trimmed = needle.trim()
        if (trimmed.isEmpty() || limit <= 0) return emptyList()

        val upperNeedle = trimmed.uppercase()
        val lowerNeedle = trimmed.lowercase()

        val results = ArrayList<Airport>(limit)
        val seenIcaos = HashSet<String>(limit * 2)

        // Tier 1: Exact ICAO match
        airportsByIcao[upperNeedle]?.let { exact ->
            results.add(exact)
            seenIcaos.add(exact.icao)
            if (results.size >= limit) return results
        }

        // Tier 2: ICAO prefix match
        for (airport in sortedAirports) {
            if (airport.icao in seenIcaos) continue
            if (airport.icao.startsWith(upperNeedle)) {
                results.add(airport)
                seenIcaos.add(airport.icao)
                if (results.size >= limit) return results
            }
        }

        // Tier 3: Name or municipality substring match
        for (airport in sortedAirports) {
            if (airport.icao in seenIcaos) continue
            val matchesName = airport.name.lowercase().contains(lowerNeedle)
            val matchesMunicipality = airport.municipality.lowercase().contains(lowerNeedle)
            if (matchesName || matchesMunicipality) {
                results.add(airport)
                seenIcaos.add(airport.icao)
                if (results.size >= limit) return results
            }
        }

        return results
    }
}
