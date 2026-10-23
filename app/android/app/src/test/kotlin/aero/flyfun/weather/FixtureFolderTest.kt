package aero.flyfun.weather

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * `app/fixtures` is a resource root of the main source set (#895), so everything in it ships in
 * the APK unless `packaging.resources.excludes` in `app/build.gradle.kts` drops it. Only `api/`
 * is the app's. A new top-level entry fails here until it is either excluded there or meant to
 * ship, and this list is updated to match.
 */
class FixtureFolderTest {

    @Test
    fun `every top-level fixture entry is shipped or excluded on purpose`() {
        // Gradle runs JVM unit tests from the module directory (app/android/app).
        val root = File("../../fixtures")
        val entries = root.list()?.filterNot { it.startsWith(".") }?.toSet()
            ?: error("app/fixtures not found from ${File(".").absolutePath}")
        val shipped = setOf("api")
        val excluded = setOf("live-scenarios", "showcase", "README.md")
        assertEquals(shipped + excluded, entries)
    }
}
