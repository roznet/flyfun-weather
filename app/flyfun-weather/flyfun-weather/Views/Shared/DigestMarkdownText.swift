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
        let lines = Self.normalize(markdown).split(separator: "\n", omittingEmptySubsequences: false)
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

    /// Force each list item onto its own line. The LLM often emits watch items /
    /// concerns as one run-on string ("1. … 2. … 3. …" or "- … - …"), so insert a
    /// newline before any numbered ("N. ") or bulleted ("- ", "• ") marker that
    /// isn't already at the start of a line. `\d+\.\s` (digit-dot-space) won't
    /// match decimals like "10.5" (no space after the dot).
    static func normalize(_ s: String) -> String {
        var out = s
        // Break before "N. " markers that follow other text on the same line.
        out = out.replacingOccurrences(
            of: "(?<=\\S)[ \\t]+(?=\\d+\\.\\s)",
            with: "\n", options: .regularExpression)
        // Break before "- " / "• " bullets mid-line.
        out = out.replacingOccurrences(
            of: "(?<=\\S)[ \\t]+(?=[-•]\\s)",
            with: "\n", options: .regularExpression)
        return out
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
