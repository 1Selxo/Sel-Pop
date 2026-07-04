from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_PROFILE_ID = 'profile-ja'
BUILTIN_DICTIONARY_ID = 'builtin-main'


@dataclass(frozen=True)
class YomitanLanguage:
    code: str
    name: str


# Mirrors Yomitan's language descriptor list in yomidevs/yomitan as of the
# implementation date. These are the languages that can be selected for local
# dictionary profiles in Sel-Pop.
YOMITAN_LANGUAGES: tuple[YomitanLanguage, ...] = (
    YomitanLanguage('xxx', 'Any / Unknown'),
    YomitanLanguage('aii', 'Assyrian Neo-Aramaic'),
    YomitanLanguage('ar', 'Arabic (MSA)'),
    YomitanLanguage('arz', 'Arabic (Egyptian)'),
    YomitanLanguage('be', 'Belarusian'),
    YomitanLanguage('bg', 'Bulgarian'),
    YomitanLanguage('cs', 'Czech'),
    YomitanLanguage('da', 'Danish'),
    YomitanLanguage('de', 'German'),
    YomitanLanguage('el', 'Greek'),
    YomitanLanguage('en', 'English'),
    YomitanLanguage('eo', 'Esperanto'),
    YomitanLanguage('es', 'Spanish'),
    YomitanLanguage('et', 'Estonian'),
    YomitanLanguage('eu', 'Basque'),
    YomitanLanguage('fa', 'Persian'),
    YomitanLanguage('fi', 'Finnish'),
    YomitanLanguage('fr', 'French'),
    YomitanLanguage('ga', 'Irish'),
    YomitanLanguage('gd', 'Scottish Gaelic'),
    YomitanLanguage('grc', 'Ancient Greek'),
    YomitanLanguage('haw', 'Hawaiian'),
    YomitanLanguage('he', 'Hebrew'),
    YomitanLanguage('hi', 'Hindi'),
    YomitanLanguage('hu', 'Hungarian'),
    YomitanLanguage('id', 'Indonesian'),
    YomitanLanguage('it', 'Italian'),
    YomitanLanguage('ja', 'Japanese'),
    YomitanLanguage('ka', 'Georgian'),
    YomitanLanguage('km', 'Khmer'),
    YomitanLanguage('kn', 'Kannada'),
    YomitanLanguage('ko', 'Korean'),
    YomitanLanguage('la', 'Latin'),
    YomitanLanguage('lo', 'Lao'),
    YomitanLanguage('lv', 'Latvian'),
    YomitanLanguage('mn', 'Mongolian'),
    YomitanLanguage('mt', 'Maltese'),
    YomitanLanguage('nl', 'Dutch'),
    YomitanLanguage('no', 'Norwegian'),
    YomitanLanguage('pl', 'Polish'),
    YomitanLanguage('pt', 'Portuguese'),
    YomitanLanguage('ro', 'Romanian'),
    YomitanLanguage('ru', 'Russian'),
    YomitanLanguage('sga', 'Old Irish'),
    YomitanLanguage('sh', 'Serbo-Croatian'),
    YomitanLanguage('sq', 'Albanian'),
    YomitanLanguage('sv', 'Swedish'),
    YomitanLanguage('th', 'Thai'),
    YomitanLanguage('tl', 'Tagalog'),
    YomitanLanguage('tok', 'Toki Pona'),
    YomitanLanguage('tr', 'Turkish'),
    YomitanLanguage('uk', 'Ukrainian'),
    YomitanLanguage('vi', 'Vietnamese'),
    YomitanLanguage('cy', 'Welsh'),
    YomitanLanguage('yi', 'Yiddish'),
    YomitanLanguage('yue', 'Cantonese'),
    YomitanLanguage('zh', 'Chinese'),
)

LANGUAGE_BY_CODE = {language.code: language for language in YOMITAN_LANGUAGES}


LATIN_LANGUAGES = {
    'cs', 'da', 'de', 'en', 'eo', 'es', 'et', 'eu', 'fi', 'fr', 'ga', 'gd',
    'grc', 'haw', 'hu', 'id', 'it', 'la', 'lv', 'mt', 'nl', 'no', 'pl',
    'pt', 'ro', 'sga', 'sh', 'sq', 'sv', 'tl', 'tok', 'tr', 'vi', 'cy',
}

CASE_LANGUAGES = LATIN_LANGUAGES | {'be', 'bg', 'el', 'mn', 'ru', 'uk'}
DIACRITIC_LANGUAGES = {'grc', 'id', 'it', 'la', 'ro', 'sga', 'tl'}
NO_SPACE_CJK_LANGUAGES = {'ja', 'zh', 'yue'}

_SCRIPT_PATTERNS: dict[str, re.Pattern[str]] = {
    'latin': re.compile(r'[A-Za-z\u00c0-\u024f\u1e00-\u1eff]'),
    'cyrillic': re.compile(r'[\u0400-\u04ff\u0500-\u052f]'),
    'greek': re.compile(r'[\u0370-\u03ff\u1f00-\u1fff]'),
    'arabic': re.compile(r'[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]'),
    'hebrew': re.compile(r'[\u0590-\u05ff]'),
    'syriac': re.compile(r'[\u0700-\u074f]'),
    'japanese': re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]'),
    'cjk': re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]'),
    'hangul': re.compile(r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]'),
    'thai': re.compile(r'[\u0e00-\u0e7f]'),
    'lao': re.compile(r'[\u0e80-\u0eff]'),
    'khmer': re.compile(r'[\u1780-\u17ff]'),
    'devanagari': re.compile(r'[\u0900-\u097f]'),
    'georgian': re.compile(r'[\u10a0-\u10ff\u1c90-\u1cbf]'),
    'kannada': re.compile(r'[\u0c80-\u0cff]'),
}

_LANGUAGE_SCRIPTS: dict[str, tuple[str, ...]] = {
    **{code: ('latin',) for code in LATIN_LANGUAGES},
    'aii': ('syriac',),
    'ar': ('arabic',),
    'arz': ('arabic',),
    'be': ('cyrillic',),
    'bg': ('cyrillic',),
    'el': ('greek',),
    'fa': ('arabic',),
    'grc': ('greek', 'latin'),
    'he': ('hebrew',),
    'hi': ('devanagari',),
    'ja': ('japanese',),
    'ka': ('georgian',),
    'km': ('khmer',),
    'kn': ('kannada',),
    'ko': ('hangul',),
    'lo': ('lao',),
    'mn': ('cyrillic',),
    'ru': ('cyrillic',),
    'th': ('thai',),
    'uk': ('cyrillic',),
    'yi': ('hebrew',),
    'yue': ('cjk',),
    'zh': ('cjk',),
}

_ARABIC_DIACRITICS_RE = re.compile(r'[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]')
_WORD_BOUNDARY_SCRIPT_RE = re.compile(
    r'[A-Za-z\u00c0-\u024f\u1e00-\u1eff\u0370-\u03ff\u0400-\u052f'
    r'\u0590-\u05ff\u0600-\u06ff\u0700-\u074f\u0900-\u097f\u10a0-\u10ff'
    r'\u1c90-\u1cbf\u0c80-\u0cff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]'
)
_EDGE_PUNCT_RE = re.compile(r"^[^\w\u00c0-\u024f\u1e00-\u1eff]+|[^\w\u00c0-\u024f\u1e00-\u1eff]+$", re.UNICODE)


def language_label(code: str | None) -> str:
    code = normalize_language_code(code)
    language = LANGUAGE_BY_CODE.get(code)
    return f'{language.name} ({language.code})' if language else f'{code}'


def normalize_language_code(code: str | None) -> str:
    code = (code or '').strip().lower()
    return code if code in LANGUAGE_BY_CODE else 'xxx'


def infer_dictionary_languages(
    metadata: dict[str, Any] | None,
    *name_or_url_hints: str,
) -> tuple[str, str]:
    """Read Yomitan language metadata, with a fallback for WTY-style names."""
    metadata = metadata or {}
    source = normalize_language_code(
        metadata.get('source_language') or metadata.get('sourceLanguage')
    )
    target = normalize_language_code(
        metadata.get('target_language') or metadata.get('targetLanguage')
    )
    if source != 'xxx' and target != 'xxx':
        return source, target

    hints = (
        metadata.get('title'),
        metadata.get('downloadUrl'),
        metadata.get('indexUrl'),
        *name_or_url_hints,
    )
    inferred_pairs: list[tuple[str, str]] = []
    for hint in hints:
        tokens = re.split(r'[^a-z]+', str(hint or '').lower())
        for first, second in zip(tokens, tokens[1:]):
            if first in LANGUAGE_BY_CODE and second in LANGUAGE_BY_CODE:
                if first != 'xxx' and second != 'xxx':
                    inferred_pairs.append((first, second))

    if inferred_pairs:
        inferred_source, inferred_target = inferred_pairs[-1]
        if source == 'xxx':
            source = inferred_source
        if target == 'xxx':
            target = inferred_target
    return source, target


def default_dictionary_profiles() -> list[dict[str, Any]]:
    return [{
        'id': DEFAULT_PROFILE_ID,
        'name': 'Japanese',
        'language': 'ja',
        'enabled': True,
    }]


def normalize_dictionary_profiles(raw_profiles: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_profiles, list) or not raw_profiles:
        return default_dictionary_profiles()

    normalized = []
    seen_ids = set()
    for index, profile in enumerate(raw_profiles):
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get('id') or f'profile-{index + 1}').strip()
        if not profile_id or profile_id in seen_ids:
            profile_id = f'profile-{index + 1}'
            suffix = 2
            while profile_id in seen_ids:
                profile_id = f'profile-{index + 1}-{suffix}'
                suffix += 1
        seen_ids.add(profile_id)
        language = normalize_language_code(profile.get('language'))
        default_name = LANGUAGE_BY_CODE.get(language, LANGUAGE_BY_CODE['xxx']).name
        name = str(profile.get('name') or default_name).strip() or default_name
        normalized.append({
            'id': profile_id,
            'name': name,
            'language': language,
            'enabled': bool(profile.get('enabled', True)),
        })

    return normalized or default_dictionary_profiles()


def enabled_profile_ids(profiles: Iterable[dict[str, Any]]) -> set[str]:
    return {str(profile.get('id')) for profile in profiles if profile.get('enabled', True)}


def set_enabled_profile_ids(raw_profiles: Any, enabled_ids: Iterable[str]) -> list[dict[str, Any]]:
    normalized = normalize_dictionary_profiles(raw_profiles)
    wanted = {str(profile_id) for profile_id in enabled_ids if str(profile_id)}
    existing = {str(profile.get('id')) for profile in normalized}
    if not wanted & existing:
        wanted = {str(normalized[0].get('id'))}
    for profile in normalized:
        profile['enabled'] = str(profile.get('id')) in wanted
    return normalized


def enable_all_profiles(raw_profiles: Any) -> list[dict[str, Any]]:
    normalized = normalize_dictionary_profiles(raw_profiles)
    for profile in normalized:
        profile['enabled'] = True
    return normalized


def enabled_profile_languages(profiles: Iterable[dict[str, Any]]) -> set[str]:
    profile_list = list(profiles)
    if not profile_list:
        return {'ja'}
    return {
        normalize_language_code(profile.get('language'))
        for profile in profile_list
        if profile.get('enabled', True)
    }


def profile_name_map(profiles: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {str(profile.get('id')): str(profile.get('name') or 'Profile') for profile in profiles}


def profile_language_map(profiles: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {
        str(profile.get('id')): normalize_language_code(profile.get('language'))
        for profile in profiles
    }


def is_text_lookup_worthy(text: str, language: str) -> bool:
    text = (text or '').strip()
    if not text:
        return False
    language = normalize_language_code(language)
    if language == 'xxx':
        return any(not char.isspace() for char in text)
    for script in _LANGUAGE_SCRIPTS.get(language, ('latin',)):
        pattern = _SCRIPT_PATTERNS.get(script)
        if pattern and pattern.search(text):
            return True
    return False


def is_text_lookup_worthy_for_languages(text: str, languages: Iterable[str]) -> bool:
    language_set = {normalize_language_code(language) for language in languages}
    return any(is_text_lookup_worthy(text, language) for language in language_set)


def should_lookup_whole_word(text: str, languages: Iterable[str]) -> bool:
    text = (text or '').strip()
    if not text or not set(languages):
        return False
    if _SCRIPT_PATTERNS['japanese'].search(text) or _SCRIPT_PATTERNS['cjk'].search(text):
        return False
    if (
        _SCRIPT_PATTERNS['thai'].search(text)
        or _SCRIPT_PATTERNS['lao'].search(text)
        or _SCRIPT_PATTERNS['khmer'].search(text)
    ):
        return False
    return bool(_WORD_BOUNDARY_SCRIPT_RE.search(text))


def clean_lookup_token(text: str) -> str:
    text = (text or '').strip()
    return _EDGE_PUNCT_RE.sub('', text)


def text_variants_for_language(text: str, language: str) -> list[str]:
    base = (text or '').strip()
    if not base:
        return []

    language = normalize_language_code(language)
    variants = [base]

    def add(value: str):
        value = (value or '').strip()
        if value and value not in variants:
            variants.append(value)

    add(unicodedata.normalize('NFKC', base))

    if language in CASE_LANGUAGES:
        add(base.lower())
        add(base[:1].upper() + base[1:] if base else base)

    if language in DIACRITIC_LANGUAGES:
        add(remove_diacritics(base))

    if language == 'de':
        add(base.replace('ß', 'ss').replace('ẞ', 'SS'))
        add(base.replace('ss', 'ß').replace('SS', 'ẞ'))

    if language in {'ar', 'arz', 'fa'}:
        add(remove_arabic_diacritics(base))
        add(remove_arabic_diacritics(base).replace('ـ', ''))

    if language == 'ru':
        add(base.replace('ё', 'е').replace('Ё', 'Е'))

    if language == 'vi':
        add(unicodedata.normalize('NFC', base))

    if language == 'ja':
        add(hira_to_kata(base))
        add(kata_to_hira(base))

    return variants


def remove_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize('NFD', text)
    return ''.join(char for char in decomposed if unicodedata.category(char) != 'Mn')


def remove_arabic_diacritics(text: str) -> str:
    return _ARABIC_DIACRITICS_RE.sub('', text)


def hira_to_kata(text: str) -> str:
    return ''.join(chr(ord(char) + 0x60) if 0x3041 <= ord(char) <= 0x3096 else char for char in text)


def kata_to_hira(text: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        elif code == 0x30FD:
            out.append('\u309d')
        elif code == 0x30FE:
            out.append('\u309e')
        else:
            out.append(char)
    return ''.join(out)
