import SwiftUI

/// Renders a markdown-lite string: the LLM digest's sections (synoptic /
/// specific concerns / trend / watch items) and the release stream's entry
/// bodies.
///
/// SwiftUI `Text` markdown is inline-only — it handles `**bold**`, `*italic*`,
/// `` `code` `` and links, but neither bullet lists nor headings. So this parses
/// each line for inline emphasis, preserves line breaks and blank-line paragraph
/// gaps, and (opt-in) draws `- ` / `• ` lines as hanging-indent bullets. Good
/// enough for both callers without pulling in a full markdown renderer.
struct MarkdownLiteText: View {
    let markdown: String
    var font: Font = .body
    var color: Color = Theme.textMuted
    /// Split run-on numbered/bulleted lists onto separate lines. True for LLM
    /// output, which often emits a whole list as a single string; pass false for
    /// authored text (release notes), which already carries real newlines and
    /// where the heuristic can only misfire.
    var normalizeRunOnLists: Bool = true
    /// Draw `- ` / `• ` lines as bullets with a hanging indent, rather than
    /// leaving the marker as literal text.
    var bulletLists: Bool = false

    var body: some View {
        let source = normalizeRunOnLists ? Self.normalize(markdown) : markdown
        let lines = source.split(separator: "\n", omittingEmptySubsequences: false)
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, raw in
                let line = String(raw)
                if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    Color.clear.frame(height: 4)  // paragraph gap
                } else if bulletLists, let item = Self.bulletContent(line) {
                    HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
                        Text("•")
                        text(item)
                    }
                    .font(font)
                    .foregroundStyle(color)
                } else {
                    text(line)
                        .font(font)
                        .foregroundStyle(color)
                }
            }
        }
    }

    private func text(_ line: String) -> some View {
        Text(Self.attributed(line))
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The content of a bullet line (`- foo` / `• foo`), or nil if the line isn't
    /// one. Leading whitespace is tolerated; the marker must be followed by a
    /// space so a lone dash or an em-dash-led sentence isn't swallowed.
    static func bulletContent(_ line: String) -> String? {
        let trimmed = line.drop(while: { $0 == " " || $0 == "\t" })
        for marker in ["- ", "• ", "* "] where trimmed.hasPrefix(marker) {
            return String(trimmed.dropFirst(marker.count))
        }
        return nil
    }

    /// Force each list item onto its own line. The LLM often emits watch items /
    /// concerns as one run-on string ("1. … 2. … 3. …" or "- … - …"), so insert a
    /// newline before any numbered ("N. ") or bulleted ("- ", "• ") marker.
    ///
    /// The marker must be preceded by a clause/sentence terminator (`. , : ; )`)
    /// so we break between list items but NOT before a sentence-final integer
    /// (e.g. "gusts to 25. Expect…" stays one line) or a range ("5 - 10 kt").
    /// `\d+\.\s` won't match decimals like "10.5" (no space after the dot).
    static func normalize(_ s: String) -> String {
        var out = s
        // Break before "N. " markers that close a previous clause/item.
        out = out.replacingOccurrences(
            of: "(?<=[.,:;)])[ \\t]+(?=\\d+\\.\\s)",
            with: "\n", options: .regularExpression)
        // Break before "- " / "• " bullets that close a previous clause/item.
        out = out.replacingOccurrences(
            of: "(?<=[.,:;)])[ \\t]+(?=[-•]\\s)",
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
