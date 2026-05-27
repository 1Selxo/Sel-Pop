# Design: Sense-level Lazy Loading + Frequency Propagation

2026-05-27 | weikipop

## Overview

Two changes touching the same rendering pipeline:

- **#4 Frequency propagation**: ensure JPDB/Jiten frequency data displays regardless of dictionary ordering (3 lines in `lookup.py`)
- **#3 Sense-level lazy loading**: only render definitions that fit in the visible popup, loading more as the user scrolls — including within a single entry

---

## Change 1: Frequency Propagation

**File:** `src/dictionary/lookup.py`

**Problem:** `_format_and_sort()` merges entries by `(written_form, reading)` into word groups across multiple dictionaries. Each entry carries its own `freq` from its source dictionary. When `popup.py` displays a group, it reads `first_entry.freq` — the first dictionary in priority order. If that dictionary lacks frequency data, no frequency shows, even though another dictionary in the group has it.

**Fix:** After merging into word groups, propagate the best (lowest) frequency across all entries in each group.

**Location:** `_format_and_sort()`, after line 669 (`word_groups[word_key].append(entry)`) and before line 670 (`processed_groups = []`).

```python
# New: propagate the best frequency across all dictionary entries in a word group.
# Ensures frequency displays regardless of which dictionary is listed first.
for entries in word_groups.values():
    best = min(e['freq'] for e in entries)
    for e in entries:
        e['freq'] = best
```

Three lines, one loop. No new concepts, no config changes.

---

## Change 2: Sense-Level Lazy Loading

**File:** `src/gui/popup.py`

### Problem

Current lazy loading works at the entry-group level: first 2 groups rendered entirely (all senses, all dictionaries), then 3 more groups per scroll trigger. A word like `読む` with 6 dictionaries × 8 senses = 48 definitions in the initial render, defeating the purpose of lazy loading.

### Design

Two-tier lazy loading: entry-group level (existing) + sense level (new).

**Data model:**

Four parallel lists, all indexed by rendered-group position:

```python
# HTML chunks — already exists, kept as-is
self._lazy_rendered_parts: list[str] = []

# NEW: raw group data for each rendered group — needed to re-render with
# expanded sense counts. Each element is the original group:
#   KanjiEntry | [word_key, [DictionaryEntry, ...]]
self._rendered_groups: list = []

# NEW: absolute group index in the full all_groups list for each rendered
# group. Used as start_index when re-rendering (for <hr> placement decision).
self._group_indices: list[int] = []

# NEW: per-group sense expansion state. Same length as the lists above.
# Each element: [(entry_idx, senses_shown, total_senses), ...]
# Only entries with unrendered senses are listed.
self._rendered_sense_state: list[list[tuple]] = []
```

**Constants (replace hardcoded values):**

```python
_SENSES_PER_ENTRY_INITIAL = 4   # how many senses to render per dict entry on first show
_SENSES_PER_LOAD         = 5   # how many more senses to render per scroll expansion
_GROUPS_PER_LOAD         = 3   # unchanged — how many new groups to load per scroll
```

**New helper — `_render_one_group()`:**

Extract single-group rendering from `_render_groups_to_html()` into its own method.

```python
def _render_one_group(self, group, group_index: int, sense_limits: dict = None):
    """Render a single entry group to HTML with optional per-entry sense limits.

    Args:
        group: KanjiEntry or [word_key, [DictionaryEntry, ...]]
        group_index: absolute position in the full group list (for <hr> decision)
        sense_limits: {entry_idx: max_senses} — when set, each dictionary entry
                      only renders up to this many senses

    Returns (html: str, sense_state: list[tuple])
        sense_state: [(entry_idx, rendered, total), ...] for entries with
                     unrendered senses remaining
    """
```

`sense_limits=None` means render all senses (backward-compatible, used by `_render_groups_to_html` for backward compat).

For initial render, caller passes `sense_limits={0: 4, 1: 4, ...}` for each dict entry.
For re-render (expansion), caller passes the updated counts from `_rendered_sense_state`.

**Modified `_render_senses()`:**

Add `max_senses: int = None` parameter. Return type changes from `(html, max_ratio)` to `(html, max_ratio, rendered: int, total: int)`. When `max_senses` is set, only renders the first N senses. The caller (`_render_one_group`) uses `rendered` and `total` to build the sense state list — entries where `rendered < total` get tracked for future expansion.

**New helper — `_initial_sense_limits()`:**

```python
def _initial_sense_limits(self, group) -> dict:
    """Build {entry_idx: _SENSES_PER_ENTRY_INITIAL} for dictionary entries
    in a group. KanjiEntry groups return an empty dict (no limiting)."""
```

Small helper, keeps `_calculate_content` clean.

**Modified `_calculate_content()`:**

Instead of hardcoded 2 initial groups, render incrementally until content fills the popup height:

```python
def _calculate_content(self, entries):
    target_height = self._fixed_popup_size().height() * 1.2
    all_groups = [...]  # same group building as before
    
    self._lazy_rendered_parts = []
    self._rendered_groups = []
    self._group_indices = []
    self._rendered_sense_state = []
    content_width = self.max_content_width
    
    for i, group in enumerate(all_groups):
        init_limits = self._initial_sense_limits(group)
        html, sense_state = self._render_one_group(
            group, i, sense_limits=init_limits
        )
        test_html = "".join(self._lazy_rendered_parts) + html
        h = self._measure_html_height(test_html, content_width)
        
        self._lazy_rendered_parts.append(html)
        self._rendered_groups.append(group)
        self._group_indices.append(i)
        self._rendered_sense_state.append(sense_state)
        
        if h > target_height and i >= 1:  # always show at least 1 group
            self._lazy_pending_groups = all_groups[i + 1:]
            self._lazy_next_group_index = i + 1
            break
    else:
        self._lazy_pending_groups = []
        self._lazy_next_group_index = len(all_groups)
    
    return "".join(self._lazy_rendered_parts)
```

This replaces the fixed `_INITIAL_RENDER_GROUPS = 2` with height-based gating. The same `_measure_html_height()` method (using QTextDocument, line 826) is already available.

**Modified `_on_scroll_lazy_load()`:**

Two-tier expansion — senses first, then groups:

```python
def _on_scroll_lazy_load(self, value: int):
    if not self._lazy_pending_groups and not any(
        any(s[1] < s[2] for s in states) for states in self._rendered_sense_state
    ):
        return
    
    sb = self.content_scroll.verticalScrollBar()
    if sb.maximum() <= 0 or value < sb.maximum() * 0.70:
        return
    
    # Tier 1: expand senses within already-rendered groups
    for g_idx, state_list in enumerate(self._rendered_sense_state):
        for s_idx, (entry_idx, shown, total) in enumerate(state_list):
            if shown < total:
                new_shown = min(shown + self._SENSES_PER_LOAD, total)
                state_list[s_idx] = (entry_idx, new_shown, total)
                # Build sense_limits dict from current state: only entries
                # that need more than the default get an explicit limit
                limits = {
                    e_idx: count for (e_idx, count, _) in state_list
                }
                group = self._rendered_groups[g_idx]
                new_html, _ = self._render_one_group(
                    group, self._group_indices[g_idx],
                    sense_limits=limits
                )
                self._lazy_rendered_parts[g_idx] = new_html
                self._commit_html()
                return  # one expansion per scroll tick
    
    # Tier 2: load new entry groups (existing logic)
    if self._lazy_pending_groups:
        self._append_next_lazy_batch()
```

**Extract `_commit_html()` helper:**

Rebuilds the full HTML from parts and applies it with scroll preservation:

```python
def _commit_html(self):
    sb = self.content_scroll.verticalScrollBar()
    saved_pos = sb.value()
    full_html = "".join(self._lazy_rendered_parts)
    self._last_html = full_html
    self.display_label.setText(full_html)
    QTimer.singleShot(0, lambda pos=saved_pos: sb.setValue(pos))
```

Extracted from `_append_next_lazy_batch()` which currently does this inline.

**Modified `_append_next_lazy_batch()`:**

When appending new groups, also populate `_rendered_groups`, `_group_indices`, and `_rendered_sense_state` with the same `_render_one_group()` call (using initial sense limits). Uses `_commit_html()` at the end instead of the current inline setText+scroll-restore.

### Compact mode skip

In compact mode, all senses for an entry are collapsed into a single `"; "`-separated line. The sense limit is not applied — compact entries are already height-efficient. The `max_senses` parameter is simply ignored when `config.compact_mode` is true.

### Scroll position preservation

The existing `QTimer.singleShot(0, ...)` pattern works because content *above* the expanded entry is unchanged. When senses are appended mid-group, the group's height increases, pushing content below it further down. The scrollbar's absolute position is restored, meaning the user sees the same logical position — the new senses appear above their viewport. This is correct: more content appeared above, and they keep reading where they were.

### Edge cases

- **Entry with 0 senses**: rendered as empty definition line, no sense state tracking
- **senses_per_entry > total**: all senses rendered, entry not tracked in sense_state
- **Single-sense entries**: no lazy expansion needed, not tracked
- **compact_mode**: sense limiting skipped entirely (already height-efficient)
- **KanjiEntry in group list**: bypassed as before, not affected by sense limiting

### Files changed

| File | Change |
|------|--------|
| `src/dictionary/lookup.py` | +3 lines — frequency propagation |
| `src/gui/popup.py` | ~80 lines changed/added — sense-level lazy loading |

---

## Testing

Manual verification:

1. **Frequency**: Configure dictionaries with JPDB as 2nd+ priority. Look up a word. Verify frequency badge `#NNNN` appears even when JPDB isn't the first dictionary.

2. **Sense lazy loading**: Look up a word with many definitions (e.g., `する`, `読む`). Verify only ~4 senses per dictionary appear initially. Scroll down — verify more senses load within the same entry before new entry groups appear.

3. **Scroll preservation**: Scroll partway down, wait for lazy expansion. Verify the content you were looking at doesn't jump.

4. **Compact mode**: Enable compact mode. Verify all senses still render inline (no lazy limiting within entries).

5. **Regression**: Look up a word with one dictionary. Verify entries display correctly, frequency shows, mining works.

---

*Co-Authored-By: chloe-chan <noreply@chloe>*
