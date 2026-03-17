import SwiftUI

/// SwiftUI view that renders a Skew-T log-P diagram for a sounding profile.
public struct SkewTView: View {
    private let renderer: SkewTRenderer

    public init(profile: SoundingProfile, config: SkewTConfiguration = .default) {
        self.renderer = SkewTRenderer(profile: profile, config: config)
    }

    public var body: some View {
        Canvas { context, size in
            renderer.render(context: &context, size: size)
        }
    }
}
