package aero.flyfun.weather.ui.briefing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.viewmodel.BriefingViewModel

/**
 * The **Discussion** tab screen (mirrors `DiscussionTabView.swift` & `briefing-charts-ios.md`):
 * - Sticky [SectionSpyBar] (`Synoptic`, `Charts`, `Concerns`, `Trend`, `Watch`, `GRAMET`).
 * - Exact order:
 *   1. `SynopticOverview` (`digest.synoptic`)
 *   2. [SurfaceChartsSection] (`pack.charts`)
 *   3. `SpecificConcerns` (`digest.specificConcerns`)
 *   4. `Trend` (`digest.trend`)
 *   5. `WatchItems` (`digest.watchItems`)
 *   6. [GrametSection] (`pack.hasGramet`)
 * - Surface charts and GRAMET render even when the digest is still loading or absent.
 */
@Composable
fun DiscussionTabScreen(
    viewModel: BriefingViewModel,
    modifier: Modifier = Modifier,
) {
    val flight by viewModel.flight.collectAsState()
    val selectedPack by viewModel.selectedPack.collectAsState()
    val advisories by viewModel.advisories.collectAsState()
    val digest by viewModel.digest.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()

    val synopticText = digest?.synoptic?.trim()?.takeIf { it.isNotEmpty() }
    val concernsText = digest?.specificConcerns?.trim()?.takeIf { it.isNotEmpty() }
    val trendText = digest?.trend?.trim()?.takeIf { it.isNotEmpty() }
    val watchMarkdown = remember(digest?.watchItems) {
        val items = digest?.watchItems?.map { it.trim() }?.filter { it.isNotEmpty() }.orEmpty()
        if (items.isEmpty()) {
            null
        } else {
            items.joinToString("\n") { item ->
                if (item.startsWith("- ") || item.startsWith("• ") || item.startsWith("* ")) {
                    item
                } else {
                    "- $item"
                }
            }
        }
    }

    val chartsManifest = remember(selectedPack?.charts) {
        val manifest = selectedPack?.charts
        if (manifest != null && (manifest.sources.isNotEmpty() || manifest.inCoverage)) {
            manifest
        } else {
            null
        }
    }
    val hasGramet = selectedPack?.hasGramet == true

    val hasAnyNarrative = synopticText != null || concernsText != null || trendText != null || watchMarkdown != null
    val packAlt = advisories?.cruiseAltitudeFt
    val flightAlt = flight?.cruiseAltitudeFt
    val hasAltitudeMismatch =
        hasAnyNarrative && packAlt != null && flightAlt != null && packAlt > 0 && flightAlt > 0 && packAlt != flightAlt

    val listState = rememberLazyListState()

    val (spySections, sectionIndices) = remember(
        hasAnyNarrative,
        hasAltitudeMismatch,
        synopticText != null,
        chartsManifest != null,
        concernsText != null,
        trendText != null,
        watchMarkdown != null,
        hasGramet,
    ) {
        val sections = mutableListOf<SpySection>()
        val indices = mutableMapOf<String, Int>()
        var nextIdx = 0

        if (!hasAnyNarrative) {
            // Digest status row occupies item 0 when no narrative is present
            nextIdx++
        } else if (hasAltitudeMismatch) {
            // Altitude mismatch banner occupies item 0 before synoptic
            nextIdx++
        }

        if (synopticText != null) {
            sections.add(SpySection("synoptic", "Synoptic"))
            indices["synoptic"] = nextIdx++
        }
        if (chartsManifest != null) {
            sections.add(SpySection("charts", "Charts"))
            indices["charts"] = nextIdx++
        }
        if (concernsText != null) {
            sections.add(SpySection("concerns", "Concerns"))
            indices["concerns"] = nextIdx++
        }
        if (trendText != null) {
            sections.add(SpySection("trend", "Trend"))
            indices["trend"] = nextIdx++
        }
        if (watchMarkdown != null) {
            sections.add(SpySection("watch", "Watch"))
            indices["watch"] = nextIdx++
        }
        if (hasGramet) {
            sections.add(SpySection("gramet", "GRAMET"))
            indices["gramet"] = nextIdx++
        }
        sections to indices
    }

    Column(modifier = modifier.fillMaxSize()) {
        SectionSpyBar(
            sections = spySections,
            listState = listState,
            sectionIndices = sectionIndices,
        )

        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            if (!hasAnyNarrative) {
                item(key = "digest_status") {
                    if (isLoading) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                            Text(
                                text = "Generating weather discussion…",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    } else {
                        Text(
                            text = "No narrative discussion is available for this briefing.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            } else if (hasAltitudeMismatch && packAlt != null && flightAlt != null) {
                item(key = "altitude_warning") {
                    DigestAltitudeWarningBanner(
                        packAltitudeFt = packAlt,
                        flightAltitudeFt = flightAlt,
                        onRecalculate = { viewModel.recalculateAdvisories(flightAlt) },
                    )
                }
            }

            // 1. Synoptic Overview
            if (synopticText != null) {
                item(key = "synoptic") {
                    DiscussionNarrativeSection(
                        title = "Synoptic Overview",
                        markdown = synopticText,
                    )
                }
            }

            // 2. Surface Charts (immediately after Synoptic Overview)
            if (chartsManifest != null) {
                item(key = "charts") {
                    SurfaceChartsSection(
                        viewModel = viewModel,
                        manifest = chartsManifest,
                    )
                }
            }

            // 3. Specific Concerns
            if (concernsText != null) {
                item(key = "concerns") {
                    DiscussionNarrativeSection(
                        title = "Specific Concerns",
                        markdown = concernsText,
                    )
                }
            }

            // 4. Trend
            if (trendText != null) {
                item(key = "trend") {
                    DiscussionNarrativeSection(
                        title = "Trend",
                        markdown = trendText,
                    )
                }
            }

            // 5. Watch Items
            if (watchMarkdown != null) {
                item(key = "watch") {
                    DiscussionNarrativeSection(
                        title = "Watch Items",
                        markdown = watchMarkdown,
                        bulletLists = true,
                    )
                }
            }

            // 6. GRAMET (last)
            if (hasGramet) {
                item(key = "gramet") {
                    GrametSection(viewModel = viewModel)
                }
            }
        }
    }
}

@Composable
private fun DiscussionNarrativeSection(
    title: String,
    markdown: String,
    bulletLists: Boolean = false,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface,
        )
        MarkdownLiteText(
            markdown = markdown,
            bulletLists = bulletLists,
        )
    }
}
