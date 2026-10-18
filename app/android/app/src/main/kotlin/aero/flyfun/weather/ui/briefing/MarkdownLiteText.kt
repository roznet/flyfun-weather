package aero.flyfun.weather.ui.briefing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp

/**
 * Renders a markdown-lite string: the LLM digest's sections (synoptic /
 * specific concerns / trend / watch items) and release stream entry bodies.
 *
 * Matches `MarkdownLiteText.swift`: supports `**bold**`, `*italic*`, `` `code` ``,
 * preserves paragraph gaps, normalizes run-on LLM lists, and draws `- ` / `• ` / `* `
 * lines as hanging-indent bullets.
 */
@Composable
fun MarkdownLiteText(
    markdown: String,
    modifier: Modifier = Modifier,
    style: TextStyle = MaterialTheme.typography.bodyMedium,
    color: Color = MaterialTheme.colorScheme.onSurfaceVariant,
    normalizeRunOnLists: Boolean = true,
    bulletLists: Boolean = true,
) {
    val codeBackground = MaterialTheme.colorScheme.surfaceVariant
    val codeColor = MaterialTheme.colorScheme.onSurface

    val lines = remember(markdown, normalizeRunOnLists) {
        val source = if (normalizeRunOnLists) normalizeMarkdownLists(markdown) else markdown
        source.split('\n')
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        lines.forEach { rawLine ->
            val trimmed = rawLine.trim()
            if (trimmed.isEmpty()) {
                Spacer(modifier = Modifier.height(4.dp))
            } else {
                val bulletItem = if (bulletLists) extractBulletContent(rawLine) else null
                if (bulletItem != null) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.Top,
                    ) {
                        Text(
                            text = "•",
                            style = style,
                            color = color,
                            modifier = Modifier.padding(end = 8.dp),
                        )
                        Text(
                            text = remember(bulletItem, codeBackground, codeColor) {
                                parseMarkdownLiteInline(bulletItem, codeBackground, codeColor)
                            },
                            style = style,
                            color = color,
                            modifier = Modifier.weight(1f),
                        )
                    }
                } else {
                    Text(
                        text = remember(rawLine, codeBackground, codeColor) {
                            parseMarkdownLiteInline(rawLine, codeBackground, codeColor)
                        },
                        style = style,
                        color = color,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }
}

private val RUN_ON_NUMBERED_REGEX = Regex("""(?<=[.,:;)])[ \t]+(?=\d+\.\s)""")
private val RUN_ON_BULLET_REGEX = Regex("""(?<=[.,:;)])[ \t]+(?=[-•]\s)""")
private val BULLET_MARKERS = listOf("- ", "• ", "* ")

/**
 * Forces each list item onto its own line when the LLM emits run-on numbered or bulleted lists.
 */
internal fun normalizeMarkdownLists(input: String): String {
    var out = input.replace(RUN_ON_NUMBERED_REGEX, "\n")
    out = out.replace(RUN_ON_BULLET_REGEX, "\n")
    return out
}

/**
 * Extracts the content of a bullet line (`- foo`, `• foo`, `* foo`), or returns `null` if not a bullet.
 */
internal fun extractBulletContent(line: String): String? {
    val trimmedStart = line.trimStart(' ', '\t')
    for (marker in BULLET_MARKERS) {
        if (trimmedStart.startsWith(marker)) {
            return trimmedStart.substring(marker.length)
        }
    }
    return null
}

/**
 * Parses inline markdown (`**bold**`, `*italic*`, `` `code` ``) into an [AnnotatedString].
 */
internal fun parseMarkdownLiteInline(
    line: String,
    codeBackground: Color = Color.Unspecified,
    codeColor: Color = Color.Unspecified,
): AnnotatedString = buildAnnotatedString {
    var i = 0
    val len = line.length
    while (i < len) {
        when {
            // Inline code: `...`
            line[i] == '`' -> {
                val closeIdx = line.indexOf('`', startIndex = i + 1)
                if (closeIdx > i + 1) {
                    withStyle(
                        SpanStyle(
                            fontFamily = FontFamily.Monospace,
                            background = codeBackground,
                            color = codeColor,
                        ),
                    ) {
                        append(line.substring(i + 1, closeIdx))
                    }
                    i = closeIdx + 1
                } else {
                    append(line[i])
                    i++
                }
            }
            // Bold: **...**
            line.startsWith("**", startIndex = i) -> {
                val closeIdx = line.indexOf("**", startIndex = i + 2)
                if (closeIdx > i + 2) {
                    withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                        append(
                            parseMarkdownLiteInline(
                                line.substring(i + 2, closeIdx),
                                codeBackground,
                                codeColor,
                            ),
                        )
                    }
                    i = closeIdx + 2
                } else {
                    append("**")
                    i += 2
                }
            }
            // Italic: *...* (not followed by space)
            line[i] == '*' && i + 1 < len && line[i + 1] != ' ' -> {
                val closeIdx = line.indexOf('*', startIndex = i + 1)
                if (closeIdx > i + 1) {
                    withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                        append(
                            parseMarkdownLiteInline(
                                line.substring(i + 1, closeIdx),
                                codeBackground,
                                codeColor,
                            ),
                        )
                    }
                    i = closeIdx + 1
                } else {
                    append(line[i])
                    i++
                }
            }
            else -> {
                append(line[i])
                i++
            }
        }
    }
}
