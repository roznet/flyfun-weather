package aero.flyfun.weather.viz

import aero.flyfun.weather.model.RouteAnalysesResponse
import aero.flyfun.weather.model.RouteFrontsResponse
import aero.flyfun.weather.model.RoutePointAnalysis
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.WindComponent
import aero.flyfun.weather.model.viz.NwpFallback
import aero.flyfun.weather.model.viz.VizDataExtractor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The air-mass boundary layer's data (experimental Hewson fronts, #196): the
 *  wire shape of `route_fronts.json`, and that the extractor hands the server's
 *  linked chains through for the rendered model only. */
class RouteFrontsTest {

    private val json = """
        {
          "schema_version": 1, "primary_level_hPa": 850, "per_model": {"ecmwf": []},
          "front_chains": {
            "ecmwf": [{
              "kind": "cold", "n_levels": 2, "tilt": "coldward",
              "nodes": [
                {"level_hPa": 925, "distance_km": 120.5, "kind": "cold", "intensity": "sharp",
                 "gradient": 3.1, "delta_theta_e": 4.2, "co_location": "convective"},
                {"level_hPa": 850, "distance_km": 131.0, "kind": "cold", "intensity": "classical",
                 "gradient": 2.8, "delta_theta_e": 3.9, "co_location": null}
              ]
            }]
          }
        }
    """.trimIndent()

    private fun manifest(model: String) = RouteAnalysesResponse(
        totalDistanceNm = 100.0,
        models = listOf(model),
        analyses = listOf(0.0, 100.0).mapIndexed { i, d ->
            RoutePointAnalysis(
                pointIndex = i,
                distanceFromOriginNm = d,
                windComponents = mapOf(
                    model to WindComponent(windSpeedKt = 5.0, windDirectionDeg = 270.0, headwindKt = 0.0, crosswindKt = 0.0),
                ),
            )
        },
    )

    @Test
    fun decodesTheServerLinkedChains() {
        val fronts = WbJson.decodeFromString(RouteFrontsResponse.serializer(), json)
        val chain = fronts.frontChains.getValue("ecmwf").single()
        assertEquals("cold", chain.kind)
        assertEquals(2, chain.nLevels)
        assertEquals(listOf(925, 850), chain.nodes.map { it.levelHpa })
        assertEquals(120.5, chain.nodes[0].distanceKm, 0.0)
        assertEquals("convective", chain.nodes[0].coLocation)
        assertNull(chain.nodes[1].coLocation)
    }

    @Test
    fun extractorPassesTheRenderedModelsChainsAndGreysOutWithout() {
        val fronts = WbJson.decodeFromString(RouteFrontsResponse.serializer(), json)

        val ecmwf = requireNotNull(
            VizDataExtractor.extractVizData(routeAnalyses = manifest("ecmwf"), selectedModel = "ecmwf", routeFronts = fronts)
        )
        assertEquals(1, ecmwf.frontChains.size)
        assertFalse("fronts-markers" in NwpFallback.getUnavailableLayers(ecmwf))

        val gfs = requireNotNull(
            VizDataExtractor.extractVizData(routeAnalyses = manifest("gfs"), selectedModel = "gfs", routeFronts = fronts)
        )
        assertTrue(gfs.frontChains.isEmpty())
        assertTrue("fronts-markers" in NwpFallback.getUnavailableLayers(gfs))
    }
}
