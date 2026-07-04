from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _server_names() -> list[str]:
    if os.name == 'nt':
        return ['sel-pop-hoshidicts-server.exe', 'weikipop-hoshidicts-server.exe']
    return ['sel-pop-hoshidicts-server', 'weikipop-hoshidicts-server']


def find_hoshidicts_server() -> Path | None:
    candidates = []
    bundle_root = getattr(sys, '_MEIPASS', None)
    for server_name in _server_names():
        if bundle_root:
            candidates.append(Path(bundle_root) / 'native' / 'bin' / server_name)
        candidates.append(Path(__file__).resolve().parents[2] / 'native' / 'bin' / server_name)
    return next((path for path in candidates if path.is_file()), None)


def import_hoshidicts_archive(zip_path: str, output_dir: str) -> dict[str, Any]:
    executable = find_hoshidicts_server()
    if executable is None:
        raise RuntimeError('HoshiDicts native server is unavailable')

    startup = None
    creation_flags = 0
    if os.name == 'nt':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creation_flags = subprocess.CREATE_NO_WINDOW

    completed = subprocess.run(
        [str(executable), '--import', str(zip_path), str(output_dir)],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=600,
        startupinfo=startup,
        creationflags=creation_flags,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(completed.stderr.strip() or 'HoshiDicts import returned no result')
    result = json.loads(lines[-1])
    if not result.get('success'):
        errors = result.get('errors') or [completed.stderr.strip() or 'Unknown import error']
        raise RuntimeError('; '.join(str(error) for error in errors))
    return result


class HoshiDictsBackend:
    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._sources_by_name: dict[str, dict[str, Any]] = {}
        atexit.register(self.close)

    @property
    def available(self) -> bool:
        return find_hoshidicts_server() is not None

    @property
    def active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def reload(self, sources: list[dict[str, Any]]):
        with self._lock:
            self.close()
            paths = []
            self._sources_by_name = {}
            for source in sources:
                path = Path(source.get('path') or '')
                if not path.is_dir() or not (path / '.hoshidicts_3').is_file():
                    continue
                paths.append(str(path))
                try:
                    index = json.loads((path / 'index.json').read_text(encoding='utf-8'))
                    dictionary_name = str(index.get('title') or source.get('name') or path.name)
                except Exception:
                    dictionary_name = str(source.get('name') or path.name)
                self._sources_by_name[dictionary_name] = source

            executable = find_hoshidicts_server()
            if not paths or executable is None:
                return

            startup = None
            creation_flags = 0
            if os.name == 'nt':
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creation_flags = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                [str(executable), *paths],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                startupinfo=startup,
                creationflags=creation_flags,
            )
            ready_line = self._process.stdout.readline() if self._process.stdout else ''
            ready = json.loads(ready_line or '{}')
            if not ready.get('ready'):
                self.close()
                raise RuntimeError('HoshiDicts native server failed to start')
            logger.info('HoshiDicts loaded %d accelerated dictionaries.', len(paths))

    def query_many(self, expressions: list[str]) -> dict[str, list[dict[str, Any]]]:
        unique = list(dict.fromkeys(value for value in expressions if value))
        if not unique or not self.active:
            return {}

        with self._lock:
            if not self.active or self._process is None:
                return {}
            try:
                self._process.stdin.write(json.dumps(unique, ensure_ascii=False) + '\n')
                self._process.stdin.flush()
                line = self._process.stdout.readline()
                payload = json.loads(line or '[]')
                if isinstance(payload, dict) and payload.get('error'):
                    raise RuntimeError(payload['error'])
                return {
                    str(item.get('query') or ''): list(item.get('terms') or [])
                    for item in payload
                }
            except Exception:
                logger.exception('HoshiDicts query failed; disabling the native backend.')
                self.close()
                return {}

    def get_media(self, dictionary_name: str, media_path: str) -> bytes | None:
        if not dictionary_name or not media_path or not self.active:
            return None
        with self._lock:
            if not self.active or self._process is None:
                return None
            try:
                request = {
                    'type': 'media',
                    'dictionary': dictionary_name,
                    'path': media_path,
                }
                self._process.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
                self._process.stdin.flush()
                payload = json.loads(self._process.stdout.readline() or '{}')
                encoded = payload.get('media') if isinstance(payload, dict) else None
                return base64.b64decode(encoded) if encoded else None
            except Exception:
                logger.exception('HoshiDicts media lookup failed for %s.', media_path)
                return None

    def source_for_dictionary(self, dictionary_name: str) -> dict[str, Any] | None:
        return self._sources_by_name.get(dictionary_name)

    def close(self):
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
