package aero.flyfun.weather.data

import aero.flyfun.weather.model.WbJson
import kotlinx.serialization.KSerializer

/**
 * The mock API responses shared with the iOS app (#895): one JSON file per endpoint response in
 * `app/fixtures/api/`, the single copy both platforms' fixture repositories decode. Edit the JSON
 * there, never a copy in code.
 *
 * `app/build.gradle.kts` adds `app/fixtures` as a Java resource root, so a file is read from the
 * classpath as `api/<name>.json` the same way in the app, its instrumented tests and the JVM unit
 * tests (no `Context`, no Robolectric). It ships in release on purpose: the login screen's
 * "Explore Demo Briefings" serves these fixtures to anyone without an account.
 */
object FixtureFiles {

    fun text(name: String): String {
        val path = "api/$name.json"
        val stream = FixtureFiles::class.java.classLoader?.getResourceAsStream(path)
            ?: error("fixture $path is not on the classpath (app/fixtures, see app/build.gradle.kts)")
        return stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
    }

    /**
     * Decode a fixture through the production [WbJson]. A fixture that stops decoding must fail
     * loudly here rather than silently emptying a screen and failing a journey with a misleading
     * "no flights" message.
     */
    fun <T> decode(serializer: KSerializer<T>, name: String): T =
        try {
            WbJson.decodeFromString(serializer, text(name))
        } catch (e: IllegalArgumentException) {
            throw IllegalStateException("fixture $name.json no longer decodes: ${e.message}", e)
        }
}
