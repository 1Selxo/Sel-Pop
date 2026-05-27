# Sense-Level Lazy Loading + Frequency Propagation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render only visible definitions in the popup (loading more on scroll) and ensure frequency data displays regardless of dictionary ordering.

**Architecture:** Extend the existing incremental-HTML lazy loading from entry-group granularity down to per-sense granularity. Four parallel lists track rendered groups' HTML, raw data, absolute indices, and per-entry sense expansion state. Frequency fix is a single loop in `_format_and_sort`.

**Tech Stack:** Python 3, PyQt6, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-27-lazy-senses-and-frequency-design.md`

---

### Task 1: Frequency propagation in `_format_and_sort`

**Files:**
- Modify: `src/dictionary/lookup.py:669`

- [ ] **Step 1: Add frequency propagation loop**

In `src/dictionary/lookup.py`, in `_format_and_sort()`, after the `word_groups` loop ends (after line 669: `word_groups[word_key].append(entry)`) and before `processed_groups = []` (line 670), add:

```python
        # Propagate the best (lowest) frequency across all dictionary entries
        # in each word group — ensures frequency displays regardless of which
        # dictionary is listed first in priority order.
        for entries in word_groups.values():
            best = min(e['freq'] for e in entries)
            for e in entries:
                e['freq'] = best
```

- [ ] **Step 2: Commit**

```bash
git add src/dictionary/lookup.py
git commit -m "fix: propagate best frequency across dictionary entries in word groups

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 2: Add data model fields and new constants to `Popup.__init__`

**Files:**
- Modify: `src/gui/popup.py:41-65` (instance variables block)

- [ ] **Step 1: Add lazy sense constants and new instance variables**

In `src/gui/popup.py`, in `__init__`, replace the existing lazy-loading variables (lines 61-65):

```python
        self._lazy_rendered_parts   = []    # accumulated HTML chunks
        self._lazy_next_group_index = 0     # absolute group index for <hr> placement
```

with:

```python
        # Lazy rendering — two-tier: entry groups + per-entry senses.
        # Four parallel lists indexed by rendered-group position:
        self._lazy_rendered_parts   = []    # HTML chunks per rendered group
        self._rendered_groups       = []    # raw group data (for re-render on expansion)
        self._group_indices         = []    # absolute group index (for <hr> decisions)
        self._rendered_sense_state  = []    # per-group: [(entry_idx, shown, total), ...]
        self._lazy_pending_groups   = []    # groups not yet rendered
        self._lazy_next_group_index = 0     # absolute group index for next batch

        # Per-entry sense limits
        self._SENSES_PER_ENTRY_INITIAL = 4  # senses rendered on first show
        self._SENSES_PER_LOAD         = 5  # senses added per scroll expansion
        self._GROUPS_PER_LOAD         = 3  # unchanged — groups added per scroll expansion
```

Also remove the existing duplicate declarations. `_lazy_pending_groups` is currently at line 62 — keep it, just unify. Remove `_dismissed_by_click` line if it's mixed in — it stays at line 66.

- [ ] **Step 2: Remove old constants**

Delete the old class-level constants at lines 736-737:
```python
    _INITIAL_RENDER_GROUPS = 2
    _GROUPS_PER_LOAD = 3
```

These are replaced by the instance attributes `_SENSES_PER_ENTRY_INITIAL`, `_SENSES_PER_LOAD`, `_GROUPS_PER_LOAD` and the height-based gating in `_calculate_content`.

- [ ] **Step 3: Commit**

```bash
git add src/gui/popup.py
git commit -m "refactor: add parallel tracking lists and sense-level lazy constants

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 3: Extract `_commit_html()` from `_append_next_lazy_batch`

**Files:**
- Modify: `src/gui/popup.py:895-917`

- [ ] **Step 1: Add `_commit_html()` method**

Add this method after `_on_scroll_lazy_load` (after line 893, before the `_append_next_lazy_batch` method at line 895):

```python
    def _commit_html(self):
        """Rebuild full HTML from parts and apply with scroll preservation."""
        sb = self.content_scroll.verticalScrollBar()
        saved_pos = sb.value()
        full_html = "".join(self._lazy_rendered_parts)
        self._last_html = full_html
        self.display_label.setText(full_html)
        QTimer.singleShot(0, lambda pos=saved_pos: sb.setValue(pos))
```

- [ ] **Step 2: Update `_append_next_lazy_batch` to use `_commit_html()`**

In `_append_next_lazy_batch` (line 895), replace the last 4 lines:

```python
        full_html       = "".join(self._lazy_rendered_parts)
        self._last_html = full_html   # keep in sync so the 60ms timer doesn't re-render
        self.display_label.setText(full_html)
        # Restore position after Qt settles the layout — content above the
        # saved position is unchanged so this keeps the view perfectly stable.
        QTimer.singleShot(0, lambda pos=saved_pos: sb.setValue(pos))
```

with:

```python
        self._commit_html()
```

- [ ] **Step 3: Commit**

```bash
git add src/gui/popup.py
git commit -m "refactor: extract _commit_html helper for scroll-preserving HTML apply

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 4: Modify `_render_senses()` to support sense limiting

**Files:**
- Modify: `src/gui/popup.py:689-733`

- [ ] **Step 1: Change `_render_senses` signature and return type**

Replace the method signature and body of `_render_senses` (lines 689-733) with:

```python
    def _render_senses(self, entry, max_ratio: float, inline_only: bool = False,
                       max_senses: int = None) -> tuple:
        """Render the definitions block for one DictionaryEntry.

        Args:
            max_senses: If set, only render the first N senses.

        Returns (senses_html, updated_max_ratio, rendered, total) where
        rendered and total are sense counts (for lazy-expansion tracking).
        """
        sense_count = len(entry.senses)
        effective_limit = max_senses if max_senses is not None else sense_count
        # Compact mode: always render all senses (already one line)
        if config.compact_mode:
            effective_limit = sense_count

        parts_calc, parts_html = [], []
        for idx, sense in enumerate(entry.senses):
            if idx >= effective_limit:
                break
            glosses   = sense.get("glosses", [])
            pos_list  = sense.get("pos",     [])
            tags_list = sense.get("tags",    [])

            gloss_str = (", ".join(glosses) if config.show_all_glosses else (glosses[0] if glosses else ""))
            s_calc = f"({idx+1})" if config.show_all_glosses else ""
            s_html = f"<b>({idx+1})</b> " if config.show_all_glosses else ""

            if config.show_pos and pos_list:
                pos_str = f' ({", ".join(pos_list)})'
                s_calc += pos_str
                s_html += f'<span style="color:{config.color_foreground};opacity:0.7;"><i>{pos_str}</i></span> '
            if config.show_tags and tags_list:
                t_str = f' [{", ".join(tags_list)}]'
                s_calc += t_str
                s_html += (f'<span style="color:{config.color_foreground};'
                           f'font-size:{config.font_size_definitions-2}px;opacity:0.7;">{t_str}</span> ')
            s_calc += gloss_str
            s_html += gloss_str
            parts_calc.append(s_calc)
            parts_html.append(s_html)

        if config.compact_mode:
            sep = "; "
            full_def_html = sep.join(parts_html)
            max_ratio = max(max_ratio, len(sep.join(parts_calc)) / self.def_chars_per_line)
        else:
            sep = "<br>"
            full_def_html = sep.join(parts_html)
            for p in parts_calc:
                max_ratio = max(max_ratio, len(p) / self.def_chars_per_line)

        if inline_only:
            senses_html = (f'<span style="font-size:{config.font_size_definitions}px;">'
                           f'{full_def_html}</span>')
        else:
            sep_space = " " if config.compact_mode else "<br>"
            senses_html = (f'{sep_space}<span style="font-size:{config.font_size_definitions}px;">'
                           f'{full_def_html}</span>')
        return senses_html, max_ratio, effective_limit, sense_count
```

- [ ] **Step 2: Update `_render_groups_to_html` for new return type**

The two call sites inside `_render_groups_to_html` (lines 795 and 804) currently unpack `senses_html, max_ratio = self._render_senses(...)`. Change them to:

Line 795:
```python
                    senses_html, max_ratio, _, _ = self._render_senses(entry, max_ratio, inline_only=True)
```

Line 804:
```python
                    senses_html, max_ratio, _, _ = self._render_senses(entry, max_ratio)
```

- [ ] **Step 3: Commit**

```bash
git add src/gui/popup.py
git commit -m "refactor: add max_senses parameter to _render_senses for sense-level limiting

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 5: Add `_initial_sense_limits()` and `_render_one_group()` methods

**Files:**
- Modify: `src/gui/popup.py` (new methods after `_render_senses`)

- [ ] **Step 1: Add `_initial_sense_limits()` helper**

Add after `_render_senses` (after line 733, before `_INITIAL_RENDER_GROUPS` / `_render_groups_to_html`):

```python
    def _initial_sense_limits(self, group) -> dict:
        """Build {entry_idx: _SENSES_PER_ENTRY_INITIAL} for dictionary entries
        in a group. KanjiEntry groups return an empty dict (no limiting)."""
        if isinstance(group, KanjiEntry):
            return {}
        _, dict_entries = group
        return {i: self._SENSES_PER_ENTRY_INITIAL for i in range(len(dict_entries))}
```

- [ ] **Step 2: Add `_render_one_group()` method**

Add after `_initial_sense_limits`:

```python
    def _render_one_group(self, group, group_index: int, sense_limits: dict = None):
        """Render a single entry group to HTML with optional per-entry sense limits.

        Args:
            group: KanjiEntry or [word_key, [DictionaryEntry, ...]]
            group_index: absolute position in the full group list (for <hr> decision)
            sense_limits: {entry_idx: max_senses} — when set, each dictionary entry
                          only renders up to this many senses. None = render all.

        Returns (html: str, sense_state: list[tuple])
            sense_state: [(entry_idx, rendered, total), ...] for entries with
                         unrendered senses remaining.
        """
        # ── Kanji entry ──────────────────────────────────────────────
        if isinstance(group, KanjiEntry):
            return self._render_kanji_entry(group), []

        # ── Dictionary entry group ───────────────────────────────────
        word_key, dict_entries = group
        first_entry = dict_entries[0]

        header_calc = first_entry.written_form or ""
        if first_entry.reading:
            header_calc += f" [{first_entry.reading}]"
        max_ratio = max(0.0, len(header_calc) / self.header_chars_per_line)

        header_html = (
            f'<span style="color:{config.color_highlight_word};'
            f'font-size:{config.font_size_header}px;">{first_entry.written_form}</span>'
        )
        if first_entry.reading:
            header_html += (
                f' <span style="color:{config.color_highlight_reading};'
                f'font-size:{config.font_size_header - 2}px;">[{first_entry.reading}]</span>'
            )
        if first_entry.deconjugation_process and config.show_deconjugation:
            dc = " ← ".join(p for p in first_entry.deconjugation_process if p)
            if dc:
                header_html += (
                    f' <span style="color:{config.color_foreground};'
                    f'font-size:{config.font_size_definitions - 2}px;opacity:0.8;">({dc})</span>'
                )
        if config.show_frequency and first_entry.freq < 999_999:
            header_html += (
                f' <span style="color:{config.color_foreground};'
                f'font-size:{config.font_size_definitions - 2}px;opacity:0.6;">#{first_entry.freq}</span>'
            )

        multi_dict = len(dict_entries) > 1
        body_parts = []
        sense_state = []

        for entry_idx, entry in enumerate(dict_entries):
            limit = (sense_limits or {}).get(entry_idx)  # None → render all
            if multi_dict:
                senses_html, max_ratio, rendered, total = self._render_senses(
                    entry, max_ratio, inline_only=True, max_senses=limit
                )
                dict_name = getattr(entry, 'dictionary_name', '') or 'Dictionary'
                dict_label = (
                    f'<span style="color:{config.color_foreground};'
                    f'font-size:{config.font_size_definitions}px;opacity:0.85;">'
                    f'<b>{dict_name}:</b> </span>'
                )
                body_parts.append(f'{dict_label}{senses_html}')
            else:
                senses_html, max_ratio, rendered, total = self._render_senses(
                    entry, max_ratio, max_senses=limit
                )
                if getattr(entry, 'dictionary_name', ''):
                    header_html += (
                        f' <span style="color:{config.color_foreground};'
                        f'font-size:{config.font_size_definitions - 2}px;opacity:0.75;">'
                        f'[{entry.dictionary_name}]</span>'
                    )
                body_parts.append(senses_html)

            if rendered < total:
                sense_state.append((entry_idx, rendered, total))

        if multi_dict:
            p_header = f'<p style="margin:0;padding:0;">{header_html}</p>'
            p_dicts = ''.join(
                f'<p style="margin:0;padding:0;margin-top:3px;">{part}</p>'
                for part in body_parts
            )
            html = p_header + p_dicts
        else:
            combined_body = body_parts[0] if body_parts else ''
            html = f"{header_html}{combined_body}"

        return html, sense_state
```

- [ ] **Step 3: Commit**

```bash
git add src/gui/popup.py
git commit -m "feat: add _render_one_group with per-entry sense limiting

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 6: Modify `_calculate_content()` for height-based initial render

**Files:**
- Modify: `src/gui/popup.py:853-880`

- [ ] **Step 1: Rewrite `_calculate_content`**

Replace the current `_calculate_content` (lines 853-880) with:

```python
    def _calculate_content(self, entries) -> 'str | None':
        """Build initial HTML for display. Renders groups incrementally until
        content fills ~1.2x the popup height, then stops. Each entry within a
        group only renders its first _SENSES_PER_ENTRY_INITIAL senses."""
        if not self.is_calibrated or not entries:
            self._lazy_pending_groups   = []
            self._lazy_rendered_parts   = []
            self._rendered_groups       = []
            self._group_indices         = []
            self._rendered_sense_state  = []
            return None

        # Build display groups: entries sharing (written_form, reading) merged.
        all_groups = []
        for entry in entries:
            if isinstance(entry, KanjiEntry):
                all_groups.append(entry)
                continue
            word_key = (entry.written_form, entry.reading)
            if all_groups and isinstance(all_groups[-1], list) and all_groups[-1][0] == word_key:
                all_groups[-1][1].append(entry)
            else:
                all_groups.append([word_key, [entry]])

        target_height = self._fixed_popup_size().height() * 1.2
        content_width = self.max_content_width

        self._lazy_rendered_parts  = []
        self._rendered_groups      = []
        self._group_indices        = []
        self._rendered_sense_state = []

        for i, group in enumerate(all_groups):
            init_limits = self._initial_sense_limits(group)
            html, sense_state = self._render_one_group(group, i, sense_limits=init_limits)
            test_html = "".join(self._lazy_rendered_parts) + html
            h = self._measure_html_height(test_html, content_width)

            self._lazy_rendered_parts.append(html)
            self._rendered_groups.append(group)
            self._group_indices.append(i)
            self._rendered_sense_state.append(sense_state)

            if h > target_height and i >= 1:  # always show at least 1 group
                self._lazy_pending_groups   = all_groups[i + 1:]
                self._lazy_next_group_index = i + 1
                break
        else:
            self._lazy_pending_groups   = []
            self._lazy_next_group_index = len(all_groups)

        return "".join(self._lazy_rendered_parts)
```

- [ ] **Step 2: Commit**

```bash
git add src/gui/popup.py
git commit -m "feat: height-based initial render with per-entry sense limits

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 7: Modify `_on_scroll_lazy_load()` for two-tier expansion

**Files:**
- Modify: `src/gui/popup.py:886-893`

- [ ] **Step 1: Rewrite `_on_scroll_lazy_load`**

Replace the current `_on_scroll_lazy_load` (lines 886-893) with:

```python
    def _on_scroll_lazy_load(self, value: int):
        """Two-tier lazy expansion: senses within rendered groups first,
        then new entry groups. Triggered at 70% scroll depth."""
        has_pending_senses = any(
            any(s[1] < s[2] for s in states)
            for states in self._rendered_sense_state
        )
        if not self._lazy_pending_groups and not has_pending_senses:
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
                    limits = {e_idx: count for (e_idx, count, _) in state_list}
                    new_html, _ = self._render_one_group(
                        self._rendered_groups[g_idx],
                        self._group_indices[g_idx],
                        sense_limits=limits,
                    )
                    self._lazy_rendered_parts[g_idx] = new_html
                    self._commit_html()
                    return  # one expansion per scroll tick

        # Tier 2: load new entry groups
        if self._lazy_pending_groups:
            self._append_next_lazy_batch()
```

- [ ] **Step 2: Commit**

```bash
git add src/gui/popup.py
git commit -m "feat: two-tier lazy loading — senses within groups, then new groups

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 8: Modify `_append_next_lazy_batch()` for new parallel lists

**Files:**
- Modify: `src/gui/popup.py` (`_append_next_lazy_batch` method)

- [ ] **Step 1: Rewrite `_append_next_lazy_batch`**

Replace the current method body with:

```python
    def _append_next_lazy_batch(self):
        """Render the next _GROUPS_PER_LOAD pending groups with initial sense
        limits, append to all four parallel tracking lists."""
        if not self._lazy_pending_groups:
            return

        batch = self._lazy_pending_groups[:self._GROUPS_PER_LOAD]
        self._lazy_pending_groups = self._lazy_pending_groups[self._GROUPS_PER_LOAD:]

        parts, groups, indices, states = [], [], [], []
        for i, group in enumerate(batch):
            g_idx = self._lazy_next_group_index + i
            init_limits = self._initial_sense_limits(group)
            html, sense_state = self._render_one_group(group, g_idx, sense_limits=init_limits)
            parts.append(html)
            groups.append(group)
            indices.append(g_idx)
            states.append(sense_state)

        self._lazy_next_group_index += len(batch)
        self._lazy_rendered_parts.extend(parts)
        self._rendered_groups.extend(groups)
        self._group_indices.extend(indices)
        self._rendered_sense_state.extend(states)

        self._commit_html()
```

- [ ] **Step 2: Commit**

```bash
git add src/gui/popup.py
git commit -m "feat: populate parallel tracking lists in _append_next_lazy_batch

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 9: Integration — verify and fix references

**Files:**
- Modify: `src/gui/popup.py`

- [ ] **Step 1: Verify `process_latest_data_loop` reference to old constants**

Search for `_INITIAL_RENDER_GROUPS` — should only remain in `_render_groups_to_html` (line 736 area, which is now unused by new code paths but kept for potential backward compat). Remove the now-unused `_INITIAL_RENDER_GROUPS` if still present as a class constant.

- [ ] **Step 2: Verify `_lazy_pending_groups` initialization**

In `__init__`, ensure `self._lazy_pending_groups = []` is initialized before `self._lazy_next_group_index = 0`. The init block from Task 2 should have this covered.

- [ ] **Step 3: Verify `_calculate_content` null reset**

In `_calculate_content`, the early return path when `not self.is_calibrated or not entries` must reset all four parallel lists. Confirmed from Task 6 step 1.

- [ ] **Step 4: Manual code review — check all `_render_senses` call sites**

Run grep to confirm all callers handle the 4-tuple return:

```bash
grep -n "_render_senses" src/gui/popup.py
```

Expected call sites:
- `_render_groups_to_html` (lines ~795, ~804) — uses `_, _` for rendered/total
- `_render_one_group` (lines added in Task 5) — uses `rendered, total` for sense_state

- [ ] **Step 5: Commit any fixes**

```bash
git add src/gui/popup.py
git commit -m "chore: cleanup old constants and verify _render_senses call sites

Co-Authored-By: chloe-chan <noreply@chloe>"
```

---

### Task 10: Manual verification

- [ ] **Step 1: Frequency independence test**

1. Configure dictionaries with JPDB as 2nd+ priority (Settings → Dictionaries → reorder)
2. Hold hotkey over a common word like `読む`
3. Verify: frequency badge `#NNNN` appears in the header even though JPDB isn't first

- [ ] **Step 2: Sense lazy loading — initial render**

1. Look up a word with many definitions (`する`, `読む`)
2. Verify: only ~4 senses per dictionary appear initially
3. Verify: popup shows content immediately (no perceptible lag vs old behavior)

- [ ] **Step 3: Sense lazy loading — scroll expansion**

1. With the popup visible, scroll down
2. Verify: more senses load within the first entry before a new entry group appears
3. Verify: the content you were reading doesn't jump when expansion happens

- [ ] **Step 4: Compact mode regression**

1. Enable compact mode in Settings
2. Look up a word with many senses
3. Verify: all senses render inline (semicolon-separated), no lazy limiting within entries

- [ ] **Step 5: General regression**

1. Look up a word with a single dictionary
2. Verify: entries display correctly, frequency shows, mine bar works
3. Mine a word, verify it updates to "Already mined"
4. Hold Alt+Wheel to scroll, verify smooth scrolling

- [ ] **Step 6: Kanji entry test**

1. Enable kanji display
2. Look up a single kanji character
3. Verify: kanji entry renders correctly with readings and meanings

---

*Co-Authored-By: chloe-chan <noreply@chloe>*
