package aero.flyfun.weather.ui.briefing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * One registrable section in a scroll-spy tab (mirrors `SpySection` on iOS).
 */
data class SpySection(
    val id: String,
    val title: String,
)

/**
 * Sticky horizontal `FilterChip` bar that highlights the currently visible
 * `LazyListState` section and scrolls smoothly to a section's item index on tap.
 * Suppresses itself when fewer than two sections are present.
 */
@Composable
fun SectionSpyBar(
    sections: List<SpySection>,
    listState: LazyListState,
    sectionIndices: Map<String, Int>,
    modifier: Modifier = Modifier,
) {
    if (sections.size <= 1) return

    val coroutineScope = rememberCoroutineScope()
    val activeSectionId by remember(sections, sectionIndices) {
        derivedStateOf {
            val firstVisible = listState.firstVisibleItemIndex
            var currentId = sections.firstOrNull()?.id
            for (section in sections) {
                val idx = sectionIndices[section.id] ?: continue
                if (idx <= firstVisible + 1) {
                    currentId = section.id
                }
            }
            currentId ?: sections.first().id
        }
    }

    SectionSpyBar(
        sections = sections,
        activeId = activeSectionId,
        onSelect = { section ->
            val targetIndex = sectionIndices[section.id]
            if (targetIndex != null) {
                coroutineScope.launch {
                    listState.animateScrollToItem(targetIndex)
                }
            }
        },
        modifier = modifier,
    )
}

/**
 * Stateless overload of [SectionSpyBar] driven by an explicit [activeId] and [onSelect] callback.
 */
@Composable
fun SectionSpyBar(
    sections: List<SpySection>,
    activeId: String?,
    onSelect: (SpySection) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (sections.size <= 1) return

    val chipRowState = rememberLazyListState()
    val effectiveActiveId = activeId ?: sections.first().id

    LaunchedEffect(effectiveActiveId, sections) {
        val index = sections.indexOfFirst { it.id == effectiveActiveId }
        if (index >= 0) {
            chipRowState.animateScrollToItem(index)
        }
    }

    Surface(
        modifier = modifier,
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.96f),
        tonalElevation = 2.dp,
    ) {
        Column {
            LazyRow(
                state = chipRowState,
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(sections, key = { it.id }) { section ->
                    val isSelected = section.id == effectiveActiveId
                    FilterChip(
                        selected = isSelected,
                        onClick = { onSelect(section) },
                        label = {
                            Text(
                                text = section.title,
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal,
                            )
                        },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                            selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
                        ),
                    )
                }
            }
            HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.outlineVariant)
        }
    }
}
