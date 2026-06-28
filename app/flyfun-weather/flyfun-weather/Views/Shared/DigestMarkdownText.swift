import SwiftUI

/// Renders an LLM-digest markdown string (synoptic / specific concerns / trend /
/// watch items). SwiftUI `Text` markdown is inline-only, so we parse each line
/// for inline emphasis/links while preserving line breaks and blank-line
/// paragraph gaps — good enough for the digest's bold-led numbered lists and
/// paragraphs without pulling in a full markdown renderer.
struct DigestMarkdownText: View {
    let markdown: String
    var font: Font = .body
    var color: Color = Theme.textMuted

    var body: some View {
        let lines = markdown.split(separator: "\n", omittingEmptySubsequences: false)
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, raw in
                let line = String(raw)
                if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    Color.clear.frame(height: 4)  // paragraph gap
                } else {
                    Text(Self.attributed(line))
                        .font(font)
                        .foregroundStyle(color)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    /// Inline-only markdown parse, preserving whitespace; falls back to the raw
    /// string if the line isn't valid markdown.
    private static func attributed(_ line: String) -> AttributedString {
        (try? AttributedString(
            markdown: line,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(line)
    }
}
