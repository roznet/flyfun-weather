"""The cancel reasons are hand-copied into the clients (#1024).

Android's `DebriefSectionRules.CANCEL_REASONS` and the web's `ALL_TAGS` /
`TAG_LABELS` (`debrief-taxonomy.ts`) repeat the server's taxonomy
(`debriefs/taxonomy.py`), ids, order and labels. iOS reads it from the help
catalog instead. This reads the two sources and fails on drift, so a renamed
or added tag shows here rather than as a stale chip label in one client.
"""

from __future__ import annotations

import re
from pathlib import Path

from weatherbrief.debriefs.taxonomy import TAG_LABELS, ConditionTag

ROOT = Path(__file__).resolve().parents[1]
ANDROID_RULES = ROOT / "app/android/app/src/main/kotlin/aero/flyfun/weather/model/rules/DebriefSectionRules.kt"
WEB_TAXONOMY = ROOT / "web/ts/components/debrief-taxonomy.ts"

SERVER = [(t.value, TAG_LABELS[t]) for t in ConditionTag]


def test_android_cancel_reasons_match_the_server():
    src = ANDROID_RULES.read_text()
    block = re.search(r"CANCEL_REASONS[^=]*=\s*listOf\((.*?)\n\s*\)", src, re.S)
    assert block, "CANCEL_REASONS not found in DebriefSectionRules.kt"
    pairs = re.findall(r'"([A-Z]+)"\s+to\s+"([^"]*)"', block.group(1))
    assert pairs == SERVER


def test_web_tags_and_labels_match_the_server():
    src = WEB_TAXONOMY.read_text()
    tags = re.search(r"export const ALL_TAGS[^=]*=\s*\[(.*?)\]", src, re.S)
    labels = re.search(r"export const TAG_LABELS[^=]*=\s*\{(.*?)\}", src, re.S)
    assert tags and labels, "ALL_TAGS / TAG_LABELS not found in debrief-taxonomy.ts"
    order = re.findall(r"'([A-Z]+)'", tags.group(1))
    label_map = dict(re.findall(r"([A-Z]+):\s*'([^']*)'", labels.group(1)))
    assert [(t, label_map.get(t)) for t in order] == SERVER
