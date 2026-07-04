# lookup.py - Optimized version
import json
import logging
import math
import os
import pickle
import re
import shutil
import threading
import time
import uuid
import zlib
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config.config import config, MAX_DICT_ENTRIES
from src.dictionary.customdict import Dictionary, WRITTEN_FORM_INDEX, READING_INDEX, FREQUENCY_INDEX, ENTRY_ID_INDEX, DEFAULT_FREQ
from src.dictionary.deconjugator import Deconjugator, Form
from src.dictionary.hoshidicts_backend import (
    HoshiDictsBackend,
    find_hoshidicts_server,
    import_hoshidicts_archive,
)
from src.dictionary.languages import (
    BUILTIN_DICTIONARY_ID,
    DEFAULT_PROFILE_ID,
    clean_lookup_token,
    enabled_profile_ids,
    infer_dictionary_languages,
    language_label,
    normalize_dictionary_profiles,
    normalize_language_code,
    profile_language_map,
    profile_name_map,
    should_lookup_whole_word,
)
from src.dictionary.yomitan_client import YomitanClient
from src.dictionary.yomitan_importer import (
    convert_yomitan_zip_to_payload,
    extract_glosses,
    read_yomitan_index,
    read_yomitan_stylesheet,
    write_payload_pickle,
)
from src.dictionary.yomitan_language_engine import expand_yomitan_forms

KANJI_REGEX = re.compile(r'[\u4e00-\u9faf]')
JAPANESE_SEPARATORS = {
    "、", "。", "「", "」", "｛", "｝", "（", "）", "【", "】",
    "『", "』", "〈", "〉", "《", "》", "：", "・", "／",
    "…", "︙", "‥", "︰", "＋", "＝", "－", "÷", "？", "！",
    "．", "～", "―", "!", "?",
}

logger = logging.getLogger(__name__)


@dataclass
class DictionaryEntry:
    id: int
    written_form: str
    reading: str
    senses: list
    freq: int
    deconjugation_process: tuple
    priority: float = 0.0
    match_len: int = 0  # Add match_len field for Yomitan entries
    dictionary_name: str = ''
    dictionary_id: str = ''
    language: str = 'ja'
    profile_name: str = ''
    dictionary_priority: int = 9999


@dataclass
class KanjiEntry:
    character: str
    meanings: List[str]
    readings: List[str]
    components: List[Dict[str, str]]
    examples: List[Dict[str, str]]


def _throttled(iterable, work_ms: float = 2.0, sleep_ms: float = 8.0):
    """Yield items from iterable while capping CPU usage.
    Works for work_ms then sleeps for sleep_ms — roughly 20% CPU regardless
    of machine speed, keeping the cursor smooth during dictionary loading."""
    work_s  = work_ms  / 1000.0
    sleep_s = sleep_ms / 1000.0
    t = time.monotonic()
    for item in iterable:
        yield item
        if time.monotonic() - t >= work_s:
            time.sleep(sleep_s)
            t = time.monotonic()


class Lookup(threading.Thread):
    def __init__(self, shared_state, popup_window):
        super().__init__(daemon=True, name="Lookup")
        self.shared_state = shared_state
        self.popup_window = popup_window
        self.last_hit_result = None
        self._dict_lock = threading.RLock()

        self.user_dictionary_dir = Path('user_dictionaries')
        self.user_dictionary_dir.mkdir(exist_ok=True)

        # entry_id -> source metadata
        self.entry_sources: Dict[int, Dict[str, Any]] = {}
        self.primary_kanji_entries: Dict[str, Dict[str, Any]] = {}

        # Cache loaded Dictionary objects by (path, mtime).  This avoids
        # re-reading and re-unpickling unchanged files (e.g. the 65 MB main
        # dict) on every import or settings-save that touches dictionaries.
        self._dict_file_cache: Dict[str, tuple] = {}  # path -> (mtime, Dictionary)

        self.dictionary = Dictionary()
        self.hoshidicts = HoshiDictsBackend()
        self.lookup_cache: OrderedDict = OrderedDict()
        self.CACHE_SIZE = 500
        config.dictionary_profiles = self.get_dictionary_profiles()
        self._load_configured_dictionaries()
        self.deconjugator = Deconjugator(self.dictionary.deconjugator_rules)

        # Lazy initialization of Yomitan client - only when needed
        self._yomitan_client: Optional[YomitanClient] = None
        self._yomitan_enabled = getattr(config, "yomitan_enabled", False)
        self._yomitan_available = None  # None = untested, True/False = cached result

    @property
    def yomitan_client(self):
        """Lazy property - only create Yomitan client if actually needed"""
        if not self._yomitan_enabled:
            return None
        if self._yomitan_client is None:
            try:
                self._yomitan_client = YomitanClient(getattr(config, "yomitan_api_url", "http://127.0.0.1:19633"))
            except Exception:
                self._yomitan_enabled = False  # Disable permanently if creation fails
                return None
        return self._yomitan_client

    def clear_cache(self):
        with self._dict_lock:
            self.lookup_cache = OrderedDict()

    def get_dictionary_profiles(self) -> List[Dict[str, Any]]:
        return normalize_dictionary_profiles(getattr(config, 'dictionary_profiles', []))

    def set_dictionary_profiles(self, profiles: List[Dict[str, Any]]):
        normalized = normalize_dictionary_profiles(profiles)
        config.dictionary_profiles = normalized

        profile_ids = {profile['id'] for profile in normalized}
        language_by_profile = profile_language_map(normalized)
        fallback_profile_id = DEFAULT_PROFILE_ID if DEFAULT_PROFILE_ID in profile_ids else normalized[0]['id']
        sources = self.get_dictionary_sources()
        for source in sources:
            profile_id = source.get('profile_id') or DEFAULT_PROFILE_ID
            if profile_id not in profile_ids:
                profile_id = fallback_profile_id
            source['profile_id'] = profile_id
            source['language'] = normalize_language_code(source.get('language') or language_by_profile.get(profile_id))
        config.dictionary_sources = sources
        config.save()
        self._load_configured_dictionaries()

    def _ensure_profile_for_language(self, language: str) -> str:
        language = normalize_language_code(language)
        profiles = self.get_dictionary_profiles()
        for profile in profiles:
            if normalize_language_code(profile.get('language')) == language:
                return profile['id']

        profile_id = f'profile-{language}'
        existing_ids = {profile.get('id') for profile in profiles}
        counter = 2
        unique_profile_id = profile_id
        while unique_profile_id in existing_ids:
            unique_profile_id = f'{profile_id}-{counter}'
            counter += 1

        profiles.append({
            'id': unique_profile_id,
            'name': language_label(language).rsplit(' ', 1)[0],
            'language': language,
            'enabled': True,
        })
        config.dictionary_profiles = profiles
        return unique_profile_id

    def _default_dictionary_sources(self) -> List[Dict[str, Any]]:
        return [{
            'id': BUILTIN_DICTIONARY_ID,
            'name': 'Main Dictionary',
            'path': 'dictionary.pkl',
            'enabled': True,
            'priority': 0,
            'kind': 'pickle',
            'builtin': True,
            'profile_id': DEFAULT_PROFILE_ID,
            'language': 'ja',
            'target_language': 'en',
        }]

    def get_dictionary_sources(self) -> List[Dict[str, Any]]:
        sources = getattr(config, 'dictionary_sources', []) or []
        if not sources:
            sources = self._default_dictionary_sources()
        profiles = self.get_dictionary_profiles()
        language_by_profile = profile_language_map(profiles)
        profile_ids = set(language_by_profile)
        fallback_profile_id = DEFAULT_PROFILE_ID if DEFAULT_PROFILE_ID in profile_ids else profiles[0]['id']
        normalized = []
        for idx, source in enumerate(sources):
            profile_id = source.get('profile_id') or DEFAULT_PROFILE_ID
            if profile_id not in profile_ids:
                profile_id = fallback_profile_id
            language = normalize_language_code(source.get('language') or language_by_profile.get(profile_id) or 'ja')
            normalized.append({
                'id': source.get('id') or str(uuid.uuid4()),
                'name': (source.get('name') or 'Dictionary').strip(),
                'path': source.get('path') or '',
                'enabled': bool(source.get('enabled', True)),
                'priority': int(source.get('priority', idx)),
                'kind': source.get('kind') or 'pickle',
                'builtin': bool(source.get('builtin', False)),
                'profile_id': profile_id,
                'language': language,
                'target_language': normalize_language_code(source.get('target_language') or 'xxx'),
            })
        sources = normalized
        return sorted(sources, key=lambda x: int(x.get('priority', 0)))

    def set_dictionary_sources(self, sources: List[Dict[str, Any]], progress_cb=None):
        profiles = self.get_dictionary_profiles()
        language_by_profile = profile_language_map(profiles)
        profile_ids = {profile['id'] for profile in profiles}
        fallback_profile_id = DEFAULT_PROFILE_ID if DEFAULT_PROFILE_ID in profile_ids else profiles[0]['id']
        normalized = []
        for idx, source in enumerate(sources):
            profile_id = source.get('profile_id') or DEFAULT_PROFILE_ID
            if profile_id not in profile_ids:
                profile_id = fallback_profile_id
            language = normalize_language_code(source.get('language') or language_by_profile.get(profile_id) or 'ja')
            normalized.append({
                'id': source.get('id') or str(uuid.uuid4()),
                'name': (source.get('name') or 'Dictionary').strip(),
                'path': source.get('path') or '',
                'enabled': bool(source.get('enabled', True)),
                'priority': idx,
                'kind': source.get('kind') or 'pickle',
                'builtin': bool(source.get('builtin', False)),
                'profile_id': profile_id,
                'language': language,
                'target_language': normalize_language_code(source.get('target_language') or 'xxx'),
            })

        has_builtin = any(s.get('builtin') for s in normalized)
        if not has_builtin:
            normalized.insert(0, self._default_dictionary_sources()[0])
            for idx, source in enumerate(normalized):
                source['priority'] = idx

        config.dictionary_sources = normalized
        config.save()
        self._load_configured_dictionaries(progress_cb=progress_cb)

    def delete_dictionary_source(self, source_id: str) -> Tuple[bool, str]:
        """Delete a dictionary source entry and its file when appropriate.

        This is intentionally separate from enable/disable toggles.
        """
        if not source_id:
            return False, 'Missing dictionary id.'

        sources = self.get_dictionary_sources()
        target = next((s for s in sources if s.get('id') == source_id), None)
        if not target:
            return False, 'Dictionary not found.'
        if target.get('builtin'):
            return False, 'Built-in dictionary cannot be deleted.'

        path = target.get('path', '')
        if path:
            try:
                p = Path(path)
                # Only remove imported dictionary files inside managed directory.
                managed_root = self.user_dictionary_dir.resolve()
                resolved_path = p.resolve()
                if p.exists() and managed_root in resolved_path.parents:
                    self.hoshidicts.close()
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
            except Exception as exc:
                return False, f'Failed to delete dictionary file: {exc}'

        remaining = [s for s in sources if s.get('id') != source_id]
        self.set_dictionary_sources(remaining)
        return True, ''

    def import_dictionary_files(
        self,
        file_paths: List[str],
        progress_cb=None,
        profile_id: Optional[str] = None,
        dictionary_profiles: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        report = {'imported': [], 'failed': [], 'skipped': []}
        if not file_paths:
            return report

        if dictionary_profiles is not None:
            config.dictionary_profiles = normalize_dictionary_profiles(dictionary_profiles)

        sources = self.get_dictionary_sources()
        existing_names = {s.get('name', '').lower(): s for s in sources}
        n_files = len(file_paths)
        profiles = self.get_dictionary_profiles()
        language_by_profile = profile_language_map(profiles)
        selected_profile_id = profile_id if profile_id in language_by_profile else None

        for i, file_path in enumerate(file_paths):
            if progress_cb:
                progress_cb(i, n_files, f"Converting {Path(file_path).name}...")
            try:
                path = Path(file_path)
                if not path.exists():
                    report['failed'].append((file_path, 'File not found'))
                    continue

                suffix = path.suffix.lower()
                if suffix not in {'.zip', '.pkl'}:
                    report['failed'].append((file_path, 'Unsupported file type'))
                    continue

                metadata = {}
                source_kind = 'pickle'
                if suffix == '.zip':
                    index_meta = read_yomitan_index(str(path))
                    suggested_name = index_meta.get('title') or path.stem
                    safe_name = self._unique_dictionary_name(suggested_name, existing_names)
                    source_name = safe_name
                    metadata = dict(index_meta)

                    if find_hoshidicts_server() is not None:
                        import_root = self.user_dictionary_dir / '.hoshidicts-import' / uuid.uuid4().hex
                        import_root.mkdir(parents=True, exist_ok=True)
                        try:
                            imported = import_hoshidicts_archive(str(path), str(import_root))
                            generated_path = Path(imported['path'])
                            out_path = self.user_dictionary_dir / f'{safe_name}.hoshidict'
                            if out_path.exists():
                                shutil.rmtree(out_path)
                            shutil.move(str(generated_path), str(out_path))
                            index_path = out_path / 'index.json'
                            native_index = json.loads(index_path.read_text(encoding='utf-8'))
                            native_index['title'] = safe_name
                            index_path.write_text(
                                json.dumps(native_index, ensure_ascii=False),
                                encoding='utf-8',
                            )
                            source_kind = 'hoshidicts'
                        except Exception as exc:
                            logger.warning('HoshiDicts import failed; using pickle fallback: %s', exc)
                            native_path = self.user_dictionary_dir / f'{safe_name}.hoshidict'
                            if native_path.exists():
                                shutil.rmtree(native_path)
                        finally:
                            if import_root.exists():
                                shutil.rmtree(import_root)

                    if source_kind != 'hoshidicts':
                        payload, _ = convert_yomitan_zip_to_payload(str(path), dict_index=0)
                        out_path = self.user_dictionary_dir / f'{safe_name}.pkl'
                        write_payload_pickle(payload, str(out_path))
                        stylesheet = read_yomitan_stylesheet(str(path))
                        if stylesheet:
                            out_path.with_suffix('.css').write_text(stylesheet, encoding='utf-8')
                else:
                    with open(path, 'rb') as file:
                        payload = pickle.load(file)
                    if 'entries' not in payload or 'lookup_map' not in payload:
                        report['failed'].append((file_path, 'Invalid dictionary pickle format'))
                        continue
                    metadata = payload.get('metadata', {})
                    source_name = self._unique_dictionary_name(path.stem, existing_names)
                    out_path = self.user_dictionary_dir / f'{source_name}.pkl'
                    shutil.copyfile(path, out_path)

                declared_source_language, target_language = infer_dictionary_languages(
                    metadata,
                    source_name,
                    path.stem,
                )
                effective_profile_id = selected_profile_id
                if effective_profile_id is None:
                    effective_profile_id = self._ensure_profile_for_language(declared_source_language)
                    profiles = self.get_dictionary_profiles()
                    language_by_profile = profile_language_map(profiles)
                source_language = normalize_language_code(language_by_profile.get(effective_profile_id))

                source = {
                    'id': str(uuid.uuid4()),
                    'name': source_name,
                    'path': str(out_path),
                    'enabled': True,
                    'priority': len(sources),
                    'kind': source_kind,
                    'builtin': False,
                    'profile_id': effective_profile_id,
                    'language': source_language,
                    'target_language': target_language,
                }
                sources.append(source)
                existing_names[source_name.lower()] = source
                report['imported'].append((file_path, source_name))
            except Exception as exc:
                report['failed'].append((file_path, str(exc)))

        # Loading phase — pass progress through so the bar updates during the
        # combined dictionary rebuild that follows the file conversion.
        def _load_progress(current, total, msg):
            if progress_cb:
                progress_cb(current, total, msg or "Loading dictionaries...")

        self.set_dictionary_sources(sources, progress_cb=_load_progress)
        return report

    @staticmethod
    def _unique_dictionary_name(base_name: str, existing_names: Dict[str, Dict[str, Any]]) -> str:
        sanitized = re.sub(r'[^\w\-\s\u3040-\u30ff\u3400-\u9fff]', '', (base_name or '').strip())
        sanitized = sanitized or 'Dictionary'
        candidate = sanitized
        counter = 2
        while candidate.lower() in existing_names:
            candidate = f'{sanitized} ({counter})'
            counter += 1
        return candidate

    def _load_configured_dictionaries(self, progress_cb=None):
        with self._dict_lock:
            sources = self.get_dictionary_sources()
            profiles = self.get_dictionary_profiles()
            enabled_profiles = enabled_profile_ids(profiles)
            profile_names = profile_name_map(profiles)

            combined_entries: Dict[int, list] = {}
            combined_lookup_map: Dict[str, list] = {}
            combined_kanji_entries: Dict[str, dict] = {}
            combined_deconj_rules: list[dict] = []
            self.entry_sources = {}

            next_entry_id = 1
            enabled_sources = [
                s for s in sources
                if s.get('enabled', True) and (s.get('profile_id') or DEFAULT_PROFILE_ID) in enabled_profiles
            ]
            native_sources = [
                {
                    **source,
                    'profile_name': profile_names.get(source.get('profile_id'), ''),
                }
                for source in enabled_sources
                if source.get('kind') == 'hoshidicts'
            ]
            self.hoshidicts.reload(native_sources)
            enabled_sources = [
                source for source in enabled_sources
                if source.get('kind') != 'hoshidicts'
            ]
            n_sources = len(enabled_sources)

            for source_index, source in enumerate(sorted(enabled_sources, key=lambda x: int(x.get('priority', 0)))):
                path = source.get('path', '')
                if not path or not os.path.exists(path):
                    logger.warning("Dictionary source '%s' missing path '%s'; skipping.", source.get('name'), path)
                    continue

                if progress_cb:
                    progress_cb(source_index, n_sources, f"Loading '{source.get('name', 'Dictionary')}'...")

                # Use cached Dictionary if the file hasn't changed — avoids
                # re-reading and re-unpickling the full file (e.g. the 65 MB main
                # dict) on every import or save that touches dictionaries.
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0
                cached = self._dict_file_cache.get(path)
                if cached and cached[0] == mtime:
                    dictionary = cached[1]
                    logger.debug("Using cached dictionary for '%s'", source.get('name'))
                else:
                    dictionary = Dictionary()
                    if not dictionary.load_dictionary(path):
                        logger.warning("Failed to load dictionary source '%s' from '%s'.", source.get('name'), path)
                        continue
                    self._dict_file_cache[path] = (mtime, dictionary)

                # Cache source metadata once — reused for every entry.
                source_meta = {
                    'dictionary_name':     source.get('name', 'Dictionary'),
                    'dictionary_id':       source.get('id', ''),
                    'dictionary_priority': source_index,
                    'language':            normalize_language_code(source.get('language')),
                    'profile_name':        profile_names.get(source.get('profile_id'), ''),
                }

                # Build id_map in one comprehension and assign sequential global IDs.
                entries_items = list(dictionary.entries.items())
                id_start = next_entry_id
                id_map = {old_id: id_start + i for i, (old_id, _) in enumerate(entries_items)}
                next_entry_id += len(entries_items)

                for i, (old_id, senses) in _throttled(enumerate(entries_items)):
                    combined_entries[id_start + i] = senses
                    self.entry_sources[id_start + i] = source_meta

                for i, (surface, map_entries) in _throttled(enumerate(dictionary.lookup_map.items())):
                    bucket = combined_lookup_map.setdefault(surface, [])
                    for map_entry in map_entries:
                        new_eid = id_map.get(map_entry[ENTRY_ID_INDEX])
                        if new_eid is None:
                            continue
                        bucket.append((
                            map_entry[WRITTEN_FORM_INDEX],
                            map_entry[READING_INDEX],
                            map_entry[FREQUENCY_INDEX],
                            new_eid,
                        ))

                if not combined_kanji_entries and dictionary.kanji_entries:
                    combined_kanji_entries = dictionary.kanji_entries

                if not combined_deconj_rules and dictionary.deconjugator_rules:
                    combined_deconj_rules = dictionary.deconjugator_rules

                if progress_cb:
                    progress_cb(source_index + 1, n_sources, "")

            self.dictionary.entries = combined_entries
            self.dictionary.lookup_map = combined_lookup_map
            self.dictionary.kanji_entries = combined_kanji_entries
            self.primary_kanji_entries = combined_kanji_entries
            self.dictionary.deconjugator_rules = combined_deconj_rules or []
            self.dictionary._is_loaded = True

            has_japanese_pickle = any(
                normalize_language_code(source.get('language')) == 'ja'
                for source in enabled_sources
            )
            if not self.dictionary.deconjugator_rules and has_japanese_pickle:
                try:
                    fallback = Dictionary()
                    if fallback.load_dictionary('dictionary.pkl'):
                        self.dictionary.deconjugator_rules = fallback.deconjugator_rules
                except Exception:
                    pass

            self.deconjugator = Deconjugator(self.dictionary.deconjugator_rules)
            self.clear_cache()

    def run(self):
        logger.debug("Lookup thread started.")
        while self.shared_state.running:
            try:
                hit_result = self.shared_state.lookup_queue.get()
                if not self.shared_state.running: 
                    break
                logger.debug("Lookup: Triggered")

                current_lookup_string = self._extract_lookup_string(hit_result)
                last_lookup_string = self._extract_lookup_string(self.last_hit_result)


                self.last_hit_result = hit_result

                # skip lookup if lookup string didnt change
                if current_lookup_string == last_lookup_string:
                    continue
                

                lookup_result = self.lookup(current_lookup_string) if current_lookup_string else None
                # Pass context to popup if supported
                try:
                    self.popup_window.set_latest_data(lookup_result, hit_result if isinstance(hit_result, dict) else None)
                except TypeError:
                    self.popup_window.set_latest_data(lookup_result)
            except Exception:
                logger.exception("An unexpected error occurred in the lookup loop. Continuing...")
        logger.debug("Lookup thread stopped.")

    def _extract_lookup_string(self, hit_result: Any) -> Optional[str]:
        if not hit_result:
            return None
        if isinstance(hit_result, dict):
            return hit_result.get("lookup_string")
        if isinstance(hit_result, str):
            return hit_result
        return None

    def lookup(self, lookup_string: str) -> List:
        if not lookup_string:
            return []
        logger.info(f"Looking up: {lookup_string}")

        # Fast path: clean the text
        text = self._trim_lookup_text(lookup_string)
        if not text:
            return []

        # Fast path: cache check (most important optimization)
        with self._dict_lock:
            if text in self.lookup_cache:
                self.lookup_cache.move_to_end(text)
                return self.lookup_cache[text]

        # Choose lookup method based on availability (cache the availability check)
        with self._dict_lock:
            results = self._fast_lookup(text)

        # Append kanji entry (cheap operation)
        if config.show_kanji and KANJI_REGEX.match(text[0]):
            kd = self.primary_kanji_entries.get(text[0])
            if kd:
                results.append(KanjiEntry(
                    character=kd['character'],
                    meanings=kd['meanings'],
                    readings=kd['readings'],
                    components=kd.get('components', []),
                    examples=kd.get('examples', []),
                ))

        # Cache results
        with self._dict_lock:
            self.lookup_cache[text] = results
            if len(self.lookup_cache) > self.CACHE_SIZE:
                self.lookup_cache.popitem(last=False)
        return results

    def _fast_lookup(self, text: str) -> List:
        """
        Optimized lookup that always uses local dictionaries and optionally
        appends Yomitan API results.
        """
        results = []
        seen_result_keys = set()
        for language in self._enabled_lookup_languages():
            for entry in self._do_lookup(text, language):
                key = (
                    getattr(entry, 'written_form', ''),
                    getattr(entry, 'reading', ''),
                    getattr(entry, 'dictionary_id', ''),
                )
                if key in seen_result_keys:
                    continue
                seen_result_keys.add(key)
                results.append(entry)

        # Check if Yomitan is usable (cached result)
        if self._yomitan_enabled:
            if self._yomitan_available is None:
                # First time - check connection (one-time cost)
                try:
                    client = self.yomitan_client
                    self._yomitan_available = client is not None and client.check_connection()
                    if not self._yomitan_available:
                        logger.debug("Yomitan not available, falling back to local dictionary")
                except Exception:
                    self._yomitan_available = False
                    self._yomitan_enabled = False
            
            if self._yomitan_available:
                yomitan_entries = self._lookup_yomitan_optimized(text)
                for entry in yomitan_entries:
                    if hasattr(entry, 'dictionary_name'):
                        entry.dictionary_name = entry.dictionary_name or 'Yomitan API'
                    else:
                        entry.dictionary_name = 'Yomitan API'
                    if hasattr(entry, 'dictionary_id'):
                        entry.dictionary_id = entry.dictionary_id or 'yomitan-api'
                    else:
                        entry.dictionary_id = 'yomitan-api'
                results.extend(yomitan_entries)

        return results[:MAX_DICT_ENTRIES]

    def _enabled_lookup_languages(self) -> List[str]:
        profiles = self.get_dictionary_profiles()
        languages = []
        for profile in profiles:
            if not profile.get('enabled', True):
                continue
            language = normalize_language_code(profile.get('language'))
            if language not in languages:
                languages.append(language)
        return languages

    @staticmethod
    def _trim_lookup_text(lookup_string: str) -> str:
        text = (lookup_string or '').strip()
        text = text[:config.max_lookup_length]
        for i, ch in enumerate(text):
            if ch in JAPANESE_SEPARATORS:
                text = text[:i]
                break
        return clean_lookup_token(text)

    def _lookup_yomitan_optimized(self, lookup_string: str) -> List[Any]:
        """
        Optimized Yomitan lookup with:
        - Early exit on perfect match
        - Minimal overhead
        - Match length tracking
        """
        if not self.yomitan_client:
            return []

        found_entries = []
        seen_keys = set()
        
        # Try exact match first (fastest path)
        exact_entries = self.yomitan_client.lookup(lookup_string) or []
        if exact_entries:
            for entry in exact_entries:
                key = (entry.written_form, entry.reading)
                if key not in seen_keys:
                    entry.match_len = len(lookup_string)
                    seen_keys.add(key)
                    found_entries.append(entry)
            # If we got an exact match, return immediately (no need to try shorter prefixes)
            if found_entries:
                return found_entries

        # No exact match - try decreasing lengths
        # Start from shorter length to avoid redundant work
        max_prefix_len = min(len(lookup_string) - 1, 20)  # Limit search depth
        for prefix_len in range(max_prefix_len, 0, -1):
            prefix = lookup_string[:prefix_len]
            entries = self.yomitan_client.lookup(prefix) or []
            if entries:
                for entry in entries:
                    key = (entry.written_form, entry.reading)
                    if key not in seen_keys:
                        entry.match_len = prefix_len
                        seen_keys.add(key)
                        found_entries.append(entry)
                
                # Stop after finding matches (Yomitan usually returns best matches first)
                if found_entries and (len(lookup_string) - prefix_len) > 3:
                    break

        return found_entries

    def _do_lookup(self, text: str, language: str = 'ja') -> List[DictionaryEntry]:
        """
        Scan all prefixes of `text` (longest first), deconjugate each, then
        look up every resulting form in the kanji / kana maps.

        Collected results are keyed by entry_id and later merged/sorted by
        (written_form, reading) in _format_and_sort. Every prefix length is
        scanned (matching the legacy lookup behavior) because the correct entry for
        the on-screen word is often only reachable via deconjugation at a
        much shorter prefix than the longest literal match — an early-exit
        cutoff based on the first match's length can prune that shorter,
        correct match before it's ever tried, causing missed lookups.
        """
        collected: Dict[int, Tuple[tuple, Form, int]] = {}
        native_collected: Dict[Tuple[str, str, str], DictionaryEntry] = {}
        found_primary_match = False
        # Per-call cache for _get_map_entries — avoids repeated hira/kata
        # conversions when the same form text appears across multiple forms.
        _map_cache: Dict[str, List] = {}

        def get_entries(form_text: str) -> List:
            hit = _map_cache.get(form_text)
            if hit is not None:
                return hit
            result = self._get_map_entries(form_text)
            _map_cache[form_text] = result
            return result

        language = normalize_language_code(language)
        for prefix in self._lookup_prefixes(text, language):
            prefix_len = len(prefix)
            forms = [
                Form(
                    text=form.text,
                    process=form.process,
                    valid_pos=form.valid_parts_of_speech,
                )
                for form in expand_yomitan_forms(language, prefix)
            ]
            if language == 'ja':
                forms.extend(self.deconjugator.deconjugate(prefix))

            deduplicated_forms = []
            seen_forms = set()
            for form in forms:
                key = (form.text, form.tags, form.valid_pos)
                if key in seen_forms:
                    continue
                seen_forms.add(key)
                deduplicated_forms.append(form)

            native_results = self.hoshidicts.query_many([form.text for form in deduplicated_forms])

            prefix_hits = []

            for form in deduplicated_forms:
                for entry in self._format_hoshidicts_terms(
                    native_results.get(form.text, []),
                    form,
                    language,
                    prefix_len,
                    text,
                ):
                    key = (entry.dictionary_id, entry.written_form, entry.reading)
                    current = native_collected.get(key)
                    if current is None or (entry.match_len, entry.priority) > (current.match_len, current.priority):
                        native_collected[key] = entry

                map_entries = get_entries(form.text)
                if not map_entries:
                    continue

                for map_entry in map_entries:
                    written = map_entry[WRITTEN_FORM_INDEX]
                    entry_id = map_entry[ENTRY_ID_INDEX]
                    source_language = normalize_language_code(
                        self.entry_sources.get(entry_id, {}).get('language')
                    )
                    if source_language != language:
                        continue

                    if written is None and KANJI_REGEX.search(form.text):
                        logger.warning(f"Skipping malformed dictionary entry: kanji key '{form.text}'")
                        continue

                    if form.valid_pos or form.tags:
                        entry_senses = self.dictionary.entries.get(entry_id, [])
                        all_pos = {p for s in entry_senses for p in s['pos']}
                        if form.valid_pos and not all_pos.intersection(form.valid_pos):
                            continue
                        if form.tags and form.tags[-1] not in all_pos:
                            continue

                    if found_primary_match and not KANJI_REGEX.search(prefix):
                        if written and KANJI_REGEX.search(written):
                            continue

                    prefix_hits.append((map_entry, form))

            if prefix_hits:
                if not found_primary_match:
                    found_primary_match = True

                for map_entry, form in prefix_hits:
                    entry_id = map_entry[ENTRY_ID_INDEX]
                    if entry_id not in collected:
                        collected[entry_id] = (map_entry, form, prefix_len)

        results = self._format_and_sort(list(collected.values()), text)
        results.extend(native_collected.values())
        results.sort(key=lambda entry: (-entry.match_len, -entry.priority, entry.dictionary_priority))
        return results[:MAX_DICT_ENTRIES]

    def _format_hoshidicts_terms(
        self,
        terms: List[Dict[str, Any]],
        form: Form,
        language: str,
        match_len: int,
        original_lookup: str,
    ) -> List[DictionaryEntry]:
        results = []
        for term in terms:
            all_pos = set(str(term.get('rules') or '').split())
            if form.valid_pos and not all_pos.intersection(form.valid_pos):
                continue
            if form.tags and form.tags[-1] not in all_pos:
                continue

            glossaries_by_dictionary = defaultdict(list)
            for glossary in term.get('glossaries') or []:
                glossaries_by_dictionary[str(glossary.get('dictionary') or 'Dictionary')].append(glossary)

            frequencies = [
                int(item['value'])
                for item in term.get('frequencies') or []
                if isinstance(item.get('value'), (int, float))
            ]
            frequency = min(frequencies, default=DEFAULT_FREQ)
            written = str(term.get('expression') or form.text)
            reading = str(term.get('reading') or '')

            for dictionary_name, glossaries in glossaries_by_dictionary.items():
                source = self.hoshidicts.source_for_dictionary(dictionary_name)
                if source is None or normalize_language_code(source.get('language')) != language:
                    continue

                senses = []
                for glossary in glossaries:
                    try:
                        definitions = json.loads(glossary.get('content') or '[]')
                    except (TypeError, ValueError):
                        definitions = [str(glossary.get('content') or '')]
                    tags = ' '.join((
                        str(glossary.get('definition_tags') or ''),
                        str(glossary.get('term_tags') or ''),
                    )).split()
                    gloss_text = extract_glosses(
                        definitions if isinstance(definitions, list) else [definitions],
                        media_loader=lambda media_path, name=dictionary_name: self.hoshidicts.get_media(
                            name, media_path
                        ),
                    )
                    if gloss_text:
                        senses.append({
                            'glosses': gloss_text,
                            'pos': sorted(all_pos),
                            'tags': tags,
                            'source': dictionary_name,
                        })
                if not senses:
                    continue

                dictionary_id = str(source.get('id') or dictionary_name)
                entry_id = zlib.crc32(f'{dictionary_id}\0{written}\0{reading}'.encode('utf-8'))
                results.append(DictionaryEntry(
                    id=entry_id,
                    written_form=written,
                    reading=reading,
                    senses=senses,
                    freq=frequency,
                    deconjugation_process=form.process,
                    priority=self._calculate_priority(written, frequency, form, match_len, original_lookup),
                    match_len=match_len,
                    dictionary_name=dictionary_name,
                    dictionary_id=dictionary_id,
                    language=language,
                    profile_name=str(source.get('profile_name') or ''),
                    dictionary_priority=int(source.get('priority', 9999)),
                ))
        return results

    @staticmethod
    def _lookup_prefixes(text: str, language: str) -> List[str]:
        if should_lookup_whole_word(text, {language}):
            prefixes = []
            candidate = text.strip()
            while candidate:
                cleaned = clean_lookup_token(candidate)
                if cleaned and cleaned not in prefixes:
                    prefixes.append(cleaned)
                match = re.search(r'\s+\S*$', candidate)
                if match is None:
                    break
                candidate = candidate[:match.start()].rstrip()
            return prefixes

        candidate = text.split(maxsplit=1)[0] if text else ''
        return [candidate[:length] for length in range(len(candidate), 0, -1)]

    def _get_map_entries(self, text: str) -> List[tuple]:
        result = self.dictionary.lookup_map.get(text, [])
        if result:
            return list(result)
        kata = self._hira_to_kata(text)
        if kata != text:
            result = self.dictionary.lookup_map.get(kata, [])
            if result:
                return list(result)
        hira = self._kata_to_hira(text)
        if hira != text:
            result = self.dictionary.lookup_map.get(hira, [])
            if result:
                return list(result)
        return []

    def _format_and_sort(
        self,
        raw: List[Tuple[tuple, Form, int]],
        original_lookup: str,
    ) -> List[DictionaryEntry]:
        merged: Dict[Tuple[str, str, str], dict] = {}

        for map_entry, form, match_len in raw:
            written = map_entry[WRITTEN_FORM_INDEX]
            reading = map_entry[READING_INDEX] or ''
            freq = map_entry[FREQUENCY_INDEX]
            entry_id = map_entry[ENTRY_ID_INDEX]
            source_meta = self.entry_sources.get(entry_id, {})
            dictionary_name = source_meta.get('dictionary_name', 'Dictionary')
            dictionary_id = source_meta.get('dictionary_id', '')
            dictionary_priority = int(source_meta.get('dictionary_priority', 9999))
            language = normalize_language_code(source_meta.get('language'))
            profile_name = source_meta.get('profile_name', '')

            entry_senses = self.dictionary.entries.get(entry_id, [])
            priority = self._calculate_priority(written, freq, form, match_len, original_lookup)

            key = (written, reading, dictionary_id or dictionary_name)
            if key not in merged:
                merged[key] = {
                    'id': entry_id,
                    'written_form': written,
                    'reading': reading,
                    'senses': list(entry_senses),
                    'freq': freq,
                    'deconjugation_process': form.process,
                    'priority': priority,
                    'match_len': match_len,
                    'dictionary_name': dictionary_name,
                    'dictionary_id': dictionary_id,
                    'dictionary_priority': dictionary_priority,
                    'language': language,
                    'profile_name': profile_name,
                }
            else:
                cur = merged[key]
                if entry_id != cur['id']:
                    cur['senses'].extend(entry_senses)
                if priority > cur['priority']:
                    cur['priority'] = priority
                    cur['id'] = entry_id
                    cur['deconjugation_process'] = form.process
                if freq < cur['freq']:
                    cur['freq'] = freq
                if match_len > cur['match_len']:
                    cur['match_len'] = match_len

        # Group entries by (written_form, reading), showing all enabled dicts.
        # Users control which dictionaries appear via the enable/disable toggles
        # in Settings → Dictionaries — no artificial per-word cap needed.
        word_groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
        for entry in merged.values():
            word_key = (entry['written_form'], entry['reading'])
            word_groups[word_key].append(entry)

        processed_groups = []
        for entries in word_groups.values():
            entries.sort(key=lambda x: x['dictionary_priority'])
            # Rank this word group by the best priority available across ALL loaded
            # dictionaries — not just the first-listed one.  This ensures that
            # main dictionary.pkl frequency data (JPDB ranks) is always used for
            # sorting 歩く vs 歩き etc. regardless of where the user placed it in
            # the dictionary order settings.
            rank_entry = max(entries, key=lambda x: (x['match_len'], x['priority']))
            processed_groups.append((-rank_entry['match_len'], -rank_entry['priority'], entries[0]['dictionary_priority'], entries))

        processed_groups.sort(key=lambda x: (x[0], x[1], x[2]))

        results = []
        for _ml, _pr, _dp, entries in processed_groups:
            for d in entries:
                results.append(DictionaryEntry(
                    id=d['id'],
                    written_form=d['written_form'],
                    reading=d['reading'],
                    senses=d['senses'],
                    freq=d['freq'],
                    deconjugation_process=d['deconjugation_process'],
                    priority=d['priority'],
                    match_len=d['match_len'],
                    dictionary_name=d['dictionary_name'],
                    dictionary_id=d['dictionary_id'],
                    language=d.get('language', 'ja'),
                    profile_name=d.get('profile_name', ''),
                    dictionary_priority=d.get('dictionary_priority', 9999),
                ))
                if len(results) >= MAX_DICT_ENTRIES:
                    return results
        return results

    def _calculate_priority(
        self,
        written_form: str,
        freq: int,
        form: Form,
        match_len: int,
        original_lookup: str,
    ) -> float:
        priority = float(match_len)

        if freq < DEFAULT_FREQ:
            priority += 10.0 * (1.0 - math.log(freq) / math.log(DEFAULT_FREQ))

        original_is_kana = not KANJI_REGEX.search(original_lookup)
        written_is_kana = not KANJI_REGEX.search(written_form) if written_form else True

        if original_is_kana:
            if written_is_kana and not form.process:
                priority += 3.0

        priority -= len(form.process)
        return priority

    def _hira_to_kata(self, text: str) -> str:
        res = []
        for c in text:
            code = ord(c)
            res.append(chr(code + 0x60) if 0x3041 <= code <= 0x3096 else c)
        return ''.join(res)

    def _kata_to_hira(self, text: str) -> str:
        res = []
        for c in text:
            code = ord(c)
            if 0x30A1 <= code <= 0x30F6:
                res.append(chr(code - 0x60))
            elif code == 0x30FD:
                res.append('\u309D')
            elif code == 0x30FE:
                res.append('\u309E')
            else:
                res.append(c)
        return ''.join(res)
