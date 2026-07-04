from __future__ import annotations

import atexit
import json
import logging
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.dictionary.languages import normalize_language_code, text_variants_for_language

logger = logging.getLogger(__name__)

_BUNDLE_PATH = Path(__file__).with_name('yomitan_language_bundle.js')
_CONTEXTS = []
_CONTEXTS_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()


@dataclass(frozen=True)
class YomitanForm:
    text: str
    process: tuple[str, ...] = ()
    processors: tuple[str, ...] = ()
    valid_parts_of_speech: tuple[str, ...] = ()


def _create_context():
    from py_mini_racer import MiniRacer

    context = MiniRacer()
    context.eval(_BUNDLE_PATH.read_text(encoding='utf-8'), timeout_sec=15)
    with _CONTEXTS_LOCK:
        _CONTEXTS.append(context)
    return context


def _get_context():
    context = getattr(_THREAD_LOCAL, 'yomitan_context', None)
    if context is None:
        context = _create_context()
        _THREAD_LOCAL.yomitan_context = context
    return context


@lru_cache(maxsize=4096)
def expand_yomitan_forms(language: str, text: str) -> tuple[YomitanForm, ...]:
    language = normalize_language_code(language)
    text = (text or '').strip()
    if not text:
        return ()

    try:
        raw = _get_context().call(
            'selPopYomitanExpandJson',
            language,
            text,
            timeout_sec=5,
        )
        rows = json.loads(raw)
        forms = []
        seen = set()
        for row in rows:
            form_text = str(row.get('text') or '').strip()
            valid_pos = tuple(str(value) for value in row.get('validPartsOfSpeech', []))
            key = (form_text, valid_pos)
            if not form_text or key in seen:
                continue
            seen.add(key)
            forms.append(YomitanForm(
                text=form_text,
                process=tuple(str(value) for value in row.get('process', [])),
                processors=tuple(str(value) for value in row.get('processors', [])),
                valid_parts_of_speech=valid_pos,
            ))
        if forms:
            return tuple(forms)
    except Exception:
        logger.exception('Embedded Yomitan language processing failed for %s.', language)

    return tuple(YomitanForm(text=value) for value in text_variants_for_language(text, language))


def get_embedded_language_info() -> list[dict]:
    raw = _get_context().call('selPopYomitanLanguageInfoJson', timeout_sec=5)
    return json.loads(raw)


def close_yomitan_language_engine():
    with _CONTEXTS_LOCK:
        contexts = list(_CONTEXTS)
        _CONTEXTS.clear()
    for context in contexts:
        try:
            context.close()
        except Exception:
            pass


atexit.register(close_yomitan_language_engine)
