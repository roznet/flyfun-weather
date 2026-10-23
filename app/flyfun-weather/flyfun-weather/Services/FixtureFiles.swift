#if DEBUG
import Foundation

/// The mock API responses shared with the Android app (#895): one JSON file per
/// endpoint response in `app/fixtures/api/`, the single copy both platforms'
/// fixture repositories decode. Edit the JSON there, never a copy in code.
///
/// The folder is a folder reference in the app target, copied to `api/` in the
/// bundle and left out of Release (`EXCLUDED_SOURCE_FILE_NAMES = api`), so the
/// fixtures exist only where `FLYFUN_MOCK` does. A simulator build also falls
/// back to the source tree (a simulator app reads host paths, as
/// `FixtureShowcase` does), but `FixtureFilesTests` pins the bundled copy so a
/// broken resource phase can't hide behind the fallback.
enum FixtureFiles {

    /// Bundle subdirectory the `app/fixtures/api` folder reference copies into.
    static let bundleSubdirectory = "api"

    /// `app/fixtures/api` in the checkout this binary was built from.
    static let sourceDirectory: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()  // → Services/
        .deletingLastPathComponent()  // → flyfun-weather/ (target sources)
        .deletingLastPathComponent()  // → app/flyfun-weather/
        .deletingLastPathComponent()  // → app/
        .appendingPathComponent("fixtures/api")

    static func bundledURL(_ name: String) -> URL? {
        Bundle.main.url(forResource: name, withExtension: "json", subdirectory: bundleSubdirectory)
    }

    static func data(_ name: String) -> Data {
        let url = bundledURL(name) ?? sourceDirectory.appendingPathComponent("\(name).json")
        do {
            return try Data(contentsOf: url)
        } catch {
            fatalError("fixture \(name).json is missing (looked in the app bundle's \(bundleSubdirectory)/ and \(url.path)): \(error)")
        }
    }

    /// Decode a fixture through the production decoder. A fixture that stops
    /// decoding must fail loudly here rather than silently emptying a screen
    /// and failing a journey with a misleading "no flights" message.
    static func decode<T: Decodable>(_ type: T.Type, _ name: String) -> T {
        do {
            return try JSONDecoder.weatherBrief.decode(T.self, from: data(name))
        } catch {
            fatalError("fixture \(name).json no longer decodes as \(T.self): \(error)")
        }
    }
}
#endif
