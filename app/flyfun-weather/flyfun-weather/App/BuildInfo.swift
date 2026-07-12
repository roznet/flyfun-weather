import Foundation

/// Identifies the binary you are actually running: marketing/build version plus
/// the compile time and git revision, read from `BuildStamp.plist` — a resource
/// written into the app bundle at build time by `scripts/stamp-build-info.sh`.
/// Surfaced in Settings ▸ Developer so a build on a device traces back to a commit.
enum BuildInfo {
    private static let stamp: [String: String] = {
        guard let url = Bundle.main.url(forResource: "BuildStamp", withExtension: "plist"),
              let data = try? Data(contentsOf: url),
              let plist = try? PropertyListSerialization.propertyList(from: data, format: nil),
              let dict = plist as? [String: String]
        else { return [:] }
        return dict
    }()

    private static func info(_ key: String) -> String? {
        (Bundle.main.infoDictionary?[key] as? String).flatMap { $0.isEmpty ? nil : $0 }
    }

    /// e.g. `1.2 (8)`
    static var version: String {
        "\(info("CFBundleShortVersionString") ?? "?") (\(info("CFBundleVersion") ?? "?"))"
    }

    /// e.g. `2026-07-12 18:04 UTC`, or nil if the stamp phase didn't run.
    static var buildDate: String? { stamp["BuildDate"] }

    /// e.g. `main @ 6dcc5b76`. A `-dirty` suffix means the app sources had
    /// uncommitted changes when this binary was compiled.
    static var revision: String? {
        guard let commit = stamp["GitCommit"] else { return nil }
        guard let branch = stamp["GitBranch"], branch != "?" else { return commit }
        return "\(branch) @ \(commit)"
    }

    /// One-line summary, safe to paste into a bug report.
    static var summary: String {
        [version, buildDate, revision].compactMap(\.self).joined(separator: " — ")
    }
}
