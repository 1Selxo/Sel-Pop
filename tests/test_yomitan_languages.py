import json
import tempfile
import threading
import unittest
import zipfile
from collections import OrderedDict
from pathlib import Path

from src.config.config import config
from src.dictionary.customdict import Dictionary
from src.dictionary.hoshidicts_backend import (
    HoshiDictsBackend,
    find_hoshidicts_server,
    import_hoshidicts_archive,
)
from src.dictionary.languages import (
    YOMITAN_LANGUAGES,
    enabled_profile_languages,
    infer_dictionary_languages,
    is_text_lookup_worthy,
    is_text_lookup_worthy_for_languages,
    normalize_dictionary_profiles,
    should_lookup_whole_word,
    text_variants_for_language,
)
from src.dictionary.lookup import Lookup
from src.dictionary.structured_content import handle_structured_content
from src.dictionary.yomitan_importer import (
    convert_yomitan_zip_to_payload,
    read_yomitan_stylesheet,
    write_payload_pickle,
)
from src.dictionary.yomitan_language_engine import expand_yomitan_forms, get_embedded_language_info
from src.ocr.providers.owocr.provider import OwocrWebsocketProvider


class LanguageProfileTests(unittest.TestCase):
    def test_duplicate_profile_ids_are_made_unique(self):
        profiles = normalize_dictionary_profiles([
            {'id': 'profile-2', 'name': 'English', 'language': 'en'},
            {'id': 'profile-2', 'name': 'German', 'language': 'de'},
        ])

        self.assertEqual(len({profile['id'] for profile in profiles}), 2)

    def test_all_disabled_profiles_do_not_enable_japanese_implicitly(self):
        profiles = [{'id': 'profile-en', 'name': 'English', 'language': 'en', 'enabled': False}]

        languages = enabled_profile_languages(profiles)

        self.assertEqual(languages, set())
        self.assertFalse(is_text_lookup_worthy_for_languages('日本語', languages))

    def test_language_specific_lookup_variants(self):
        self.assertIn('strasse', text_variants_for_language('straße', 'de'))
        self.assertIn('елка', text_variants_for_language('ёлка', 'ru'))


class YomitanImporterTests(unittest.TestCase):
    @staticmethod
    def _write_dictionary_archive(path: Path, source_language: str = 'en'):
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('index.json', json.dumps({
                'title': 'English Test',
                'revision': '1',
                'format': 3,
                'sourceLanguage': source_language,
                'targetLanguage': 'de',
            }))
            archive.writestr('term_bank_1.json', json.dumps([
                ['run', 'run', '', 'v', 0, ['to move quickly'], 1, ''],
            ]))

    def test_index_language_metadata_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / 'english.zip'
            self._write_dictionary_archive(archive_path)

            payload, title = convert_yomitan_zip_to_payload(str(archive_path))

        self.assertEqual(title, 'English Test')
        self.assertEqual(payload['metadata']['source_language'], 'en')
        self.assertEqual(payload['metadata']['target_language'], 'de')
        self.assertIn('run', payload['lookup_map'])

    def test_wty_title_supplies_missing_language_metadata(self):
        source, target = infer_dictionary_languages({
            'title': 'wty-en-de',
            'downloadUrl': 'https://example.test/dict/en/de/wty-en-de.zip',
        })

        self.assertEqual(source, 'en')
        self.assertEqual(target, 'de')

    def test_dictionary_stylesheet_is_read_from_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / 'styled.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('styles.css', '[data-sc-kind="note"] { color: red; }')

            stylesheet = read_yomitan_stylesheet(str(archive_path))

        self.assertIn('color: red', stylesheet)

    def test_unsaved_selected_profile_is_used_for_import(self):
        old_profiles = config.dictionary_profiles
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                archive_path = temp_path / 'mislabeled.zip'
                self._write_dictionary_archive(archive_path, source_language='ja')

                captured_sources = []
                lookup = Lookup.__new__(Lookup)
                lookup.user_dictionary_dir = temp_path
                lookup.get_dictionary_sources = lambda: []
                lookup.get_dictionary_profiles = lambda: normalize_dictionary_profiles(config.dictionary_profiles)
                lookup.set_dictionary_sources = lambda sources, progress_cb=None: captured_sources.extend(sources)

                report = lookup.import_dictionary_files(
                    [str(archive_path)],
                    profile_id='profile-en-new',
                    dictionary_profiles=[{
                        'id': 'profile-en-new',
                        'name': 'English',
                        'language': 'en',
                        'enabled': True,
                    }],
                )

            self.assertEqual(len(report['imported']), 1)
            self.assertEqual(captured_sources[0]['profile_id'], 'profile-en-new')
            self.assertEqual(captured_sources[0]['language'], 'en')
        finally:
            config.dictionary_profiles = old_profiles


class MultiLanguageLookupTests(unittest.TestCase):
    @staticmethod
    def _write_payload(path: Path, term: str, gloss: str, pos=None):
        write_payload_pickle({
            'entries': {1: [{'glosses': [gloss], 'pos': pos or [], 'tags': []}]},
            'lookup_map': {term: [(term, term, 1, 1)]},
            'kanji_entries': {},
            'deconjugator_rules': [{'type': 'substitution'}],
        }, str(path))

    def test_two_enabled_profiles_load_together(self):
        old_profiles = config.dictionary_profiles
        old_sources = config.dictionary_sources
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                english_path = temp_path / 'english.pkl'
                german_path = temp_path / 'german.pkl'
                self._write_payload(english_path, 'run', 'move quickly', ['v'])
                self._write_payload(german_path, 'laufen', 'to run')

                config.dictionary_profiles = [
                    {'id': 'profile-en', 'name': 'English', 'language': 'en', 'enabled': True},
                    {'id': 'profile-de', 'name': 'German', 'language': 'de', 'enabled': True},
                ]
                config.dictionary_sources = [
                    {
                        'id': 'dict-en', 'name': 'English', 'path': str(english_path),
                        'enabled': True, 'priority': 0, 'profile_id': 'profile-en', 'language': 'en',
                    },
                    {
                        'id': 'dict-de', 'name': 'German', 'path': str(german_path),
                        'enabled': True, 'priority': 1, 'profile_id': 'profile-de', 'language': 'de',
                    },
                ]

                lookup = Lookup.__new__(Lookup)
                lookup._dict_lock = threading.RLock()
                lookup._dict_file_cache = {}
                lookup.dictionary = Dictionary()
                lookup.hoshidicts = HoshiDictsBackend()
                lookup.lookup_cache = OrderedDict()
                lookup.CACHE_SIZE = 500
                lookup.entry_sources = {}
                lookup.primary_kanji_entries = {}
                lookup._yomitan_enabled = False
                lookup._load_configured_dictionaries()

                english_results = lookup.lookup('run')
                inflected_english_results = lookup.lookup('running quickly')
                german_results = lookup.lookup('laufen')

            self.assertEqual(english_results[0].profile_name, 'English')
            self.assertEqual(english_results[0].language, 'en')
            self.assertEqual(inflected_english_results[0].written_form, 'run')
            self.assertIn('ing', inflected_english_results[0].deconjugation_process)
            self.assertEqual(german_results[0].profile_name, 'German')
            self.assertEqual(german_results[0].language, 'de')
        finally:
            config.dictionary_profiles = old_profiles
            config.dictionary_sources = old_sources


class HoshiDictsBackendTests(unittest.TestCase):
    @unittest.skipUnless(find_hoshidicts_server(), 'HoshiDicts native worker is not built')
    def test_native_backend_uses_embedded_yomitan_forms(self):
        old_profiles = config.dictionary_profiles
        old_sources = config.dictionary_sources
        backend = HoshiDictsBackend()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                archive_path = temp_path / 'english.zip'
                YomitanImporterTests._write_dictionary_archive(archive_path)
                imported = import_hoshidicts_archive(str(archive_path), str(temp_path))

                config.dictionary_profiles = [
                    {'id': 'profile-en', 'name': 'English', 'language': 'en', 'enabled': True},
                ]
                config.dictionary_sources = [{
                    'id': 'dict-en-native',
                    'name': 'English Test',
                    'path': imported['path'],
                    'enabled': True,
                    'priority': 0,
                    'kind': 'hoshidicts',
                    'profile_id': 'profile-en',
                    'language': 'en',
                }]

                lookup = Lookup.__new__(Lookup)
                lookup._dict_lock = threading.RLock()
                lookup._dict_file_cache = {}
                lookup.dictionary = Dictionary()
                lookup.hoshidicts = backend
                lookup.lookup_cache = OrderedDict()
                lookup.CACHE_SIZE = 500
                lookup.entry_sources = {}
                lookup.primary_kanji_entries = {}
                lookup._yomitan_enabled = False
                lookup._load_configured_dictionaries()

                results = lookup.lookup('running')

            self.assertEqual(results[0].written_form, 'run')
            self.assertEqual(results[0].dictionary_id, 'dict-en-native')
            self.assertIn('ing', results[0].deconjugation_process)
        finally:
            backend.close()
            config.dictionary_profiles = old_profiles
            config.dictionary_sources = old_sources

    @unittest.skipUnless(find_hoshidicts_server(), 'HoshiDicts native worker is not built')
    def test_native_backend_serves_dictionary_media(self):
        backend = HoshiDictsBackend()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                archive_path = temp_path / 'media.zip'
                with zipfile.ZipFile(archive_path, 'w') as archive:
                    archive.writestr('index.json', json.dumps({
                        'title': 'Media Test',
                        'revision': '1',
                        'format': 3,
                    }))
                    archive.writestr('term_bank_1.json', json.dumps([
                        ['image', '', '', '', 0, [{
                            'type': 'structured-content',
                            'content': {'tag': 'img', 'path': 'media/tiny.png'},
                        }], 1, ''],
                    ]))
                    archive.writestr('media/tiny.png', b'not-a-real-png-but-byte-exact')
                imported = import_hoshidicts_archive(str(archive_path), str(temp_path))
                backend.reload([{'name': 'Media Test', 'path': imported['path']}])

                media = backend.get_media('Media Test', 'media/tiny.png')

            self.assertEqual(media, b'not-a-real-png-but-byte-exact')
        finally:
            backend.close()


class StructuredContentTests(unittest.TestCase):
    def test_complex_yomitan_markup_preserves_css_contract(self):
        html = handle_structured_content({
            'type': 'structured-content',
            'content': {
                'tag': 'details',
                'open': True,
                'data': {
                    'content': 'etymology',
                    'someValue': 'kept',
                    'bad\" onclick': 'discarded',
                },
                'style': {'marginTop': 0.5, 'fontWeight': 'bold'},
                'content': [
                    {'tag': 'summary', 'content': 'Origin'},
                    {'tag': 'table', 'content': [
                        {'tag': 'tr', 'content': [
                            {'tag': 'td', 'colSpan': 2, 'content': 'value'},
                        ]},
                    ]},
                ],
            },
        })[0]

        self.assertIn('data-sc-content="etymology"', html)
        self.assertIn('data-sc-some-value="kept"', html)
        self.assertNotIn('onclick', html)
        self.assertIn('margin-top:0.5em', html)
        self.assertIn('font-weight:bold', html)
        self.assertIn('<details ', html)
        self.assertIn(' open>', html)
        self.assertIn('class="gloss-sc-table-container"', html)
        self.assertIn('colspan="2"', html)

    def test_dictionary_image_is_embedded_as_data_url(self):
        html = handle_structured_content({
            'type': 'structured-content',
            'content': {'tag': 'img', 'path': 'image.png', 'alt': 'diagram'},
        }, media_loader=lambda _path: b'png-data')[0]

        self.assertIn('src="data:image/png;base64,cG5nLWRhdGE="', html)
        self.assertIn('alt="diagram"', html)


class EmbeddedYomitanEngineTests(unittest.TestCase):
    def test_bundle_contains_every_current_yomitan_language(self):
        info = get_embedded_language_info()

        self.assertEqual(len(info), 57)
        self.assertEqual({row['iso'] for row in info}, {language.code for language in YOMITAN_LANGUAGES})

    def test_representative_transform_for_every_transform_enabled_language(self):
        cases = [
            ('ar', '\u0648\u0628\u064a\u062a', '\u0628\u064a\u062a'),
            ('arz', '\u0648\u0628\u064a\u062a', '\u0628\u064a\u062a'),
            ('de', 'reinigung', 'reinigen'),
            (
                'el',
                '\u03be\u03b1\u03bd\u03b1\u03c1\u03ce\u03c4\u03b7\u03c3\u03b5',
                '\u03c1\u03ce\u03c4\u03b7\u03c3\u03b5',
            ),
            ('en', 'cats', 'cat'),
            ('eo', 'amikon', 'amiko'),
            ('es', 'gatos', 'gato'),
            ('eu', 'etxeak', 'etxe'),
            ('fr', 'parlions', 'parler'),
            ('ga', 'dtriail', 'triail'),
            ('grc', '\u03bb\u03cd\u03b5\u03b9\u03c2', '\u03bb\u03cd\u03c9'),
            ('ja', '\u611b\u3057\u305d\u3046', '\u611b\u3057\u3044'),
            ('ka', '\u10ec\u10d8\u10d2\u10dc\u10d4\u10d1\u10d8', '\u10ec\u10d8\u10d2\u10dc\u10d8'),
            ('ko', '\uac11\ub2c8\ub2e4', '\uac00\ub2e4'),
            ('la', 'fluvii', 'fluvius'),
            ('sga', 'find', 'finn'),
            ('sq', 'fshin', 'fshij'),
            ('tl', 'tagaluto', 'luto'),
            ('yi', '\u05d2\u05e8\u05d5\u05e4\u05bc\u05e2\u05e1', '\u05d2\u05e8\u05d5\u05e4\u05e2'),
        ]

        for language, source, expected in cases:
            with self.subTest(language=language, source=source):
                forms = expand_yomitan_forms(language, source)
                self.assertIn(expected, {form.text for form in forms})


class MultilingualOcrTests(unittest.TestCase):
    def test_supported_scripts_are_accepted_for_their_profiles(self):
        samples = [
            ('en', 'running'),
            ('ru', '\u0431\u0435\u0436\u0430\u0442\u044c'),
            ('el', '\u03b4\u03b9\u03b1\u03b2\u03ac\u03b6\u03c9'),
            ('ar', '\u064a\u0642\u0631\u0623'),
            ('he', '\u05e7\u05d5\u05e8\u05d0'),
            ('aii', '\u071f\u072c\u0712'),
            ('hi', '\u092a\u0922\u093c\u0928\u093e'),
            ('ka', '\u10d9\u10d8\u10d7\u10ee\u10d5\u10d0'),
            ('kn', '\u0c93\u0ca6\u0cc1'),
            ('km', '\u17a2\u17b6\u1793'),
            ('ko', '\uc77d\ub2e4'),
            ('lo', '\u0ead\u0ec8\u0eb2\u0e99'),
            ('th', '\u0e2d\u0e48\u0e32\u0e19'),
            ('ja', '\u8aad\u3080'),
            ('zh', '\u8b80'),
        ]

        for language, text in samples:
            with self.subTest(language=language):
                self.assertTrue(is_text_lookup_worthy(text, language))

        self.assertTrue(should_lookup_whole_word('\uc77d\ub2e4', {'ko'}))

    def test_owocr_preserves_spaces_for_word_based_profiles(self):
        old_profiles = config.dictionary_profiles
        try:
            config.dictionary_profiles = [
                {'id': 'profile-en', 'name': 'English', 'language': 'en', 'enabled': True},
            ]
            provider = OwocrWebsocketProvider()
            result = provider._transform_to_sel_pop_format({
                'paragraphs': [{
                    'writing_direction': 'LEFT_TO_RIGHT',
                    'lines': [{
                        'bounding_box': {'center_x': 0.5, 'center_y': 0.5, 'width': 0.8, 'height': 0.1},
                        'words': [
                            {
                                'text': 'running',
                                'bounding_box': {'center_x': 0.3, 'center_y': 0.5, 'width': 0.2, 'height': 0.1},
                            },
                            {
                                'text': 'quickly',
                                'bounding_box': {'center_x': 0.6, 'center_y': 0.5, 'width': 0.2, 'height': 0.1},
                            },
                        ],
                    }],
                }],
            })

            self.assertEqual(result[0].full_text, 'running quickly')
            self.assertEqual(result[0].words[0].separator, ' ')
        finally:
            config.dictionary_profiles = old_profiles


if __name__ == '__main__':
    unittest.main()
