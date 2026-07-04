from __future__ import annotations

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView


class _LocalOnlyInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme().lower() not in {'about', 'data'}:
            info.block(True)


class _DictionaryPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.scheme().lower() in {'http', 'https'}:
                QDesktopServices.openUrl(url)
            return False
        return url.scheme().lower() in {'about', 'data'}


class DictionaryWebView(QWebEngineView):
    near_bottom = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile = QWebEngineProfile(self)
        self._interceptor = _LocalOnlyInterceptor(self._profile)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._page = _DictionaryPage(self._profile, self)
        self.setPage(self._page)

        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, False)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet('background: transparent; border: none;')
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))

        self._restore_scroll_y = 0
        self._near_bottom_sent = False
        self.page().loadFinished.connect(self._on_load_finished)
        self.page().scrollPositionChanged.connect(self._on_scroll_position_changed)

    def set_document(self, html: str, preserve_scroll: bool = False):
        self._restore_scroll_y = int(self.page().scrollPosition().y()) if preserve_scroll else 0
        self._near_bottom_sent = False
        self.setHtml(html, QUrl('about:blank'))

    def reset_scroll(self):
        self._restore_scroll_y = 0
        self.page().runJavaScript('window.scrollTo(0, 0)')

    def scroll_by(self, pixels: int):
        self.page().runJavaScript(f'window.scrollBy(0, {int(pixels)})')

    def _on_load_finished(self, _ok: bool):
        y = self._restore_scroll_y
        self.page().runJavaScript(f'window.scrollTo(0, {y})')

    def _on_scroll_position_changed(self, position):
        if self._near_bottom_sent:
            return
        content_height = self.page().contentsSize().height()
        if content_height <= 0:
            return
        if position.y() + self.height() >= content_height * 0.70:
            self._near_bottom_sent = True
            self.near_bottom.emit()


SHADOW_BASE_CSS = r'''
:host { display: block; color: inherit; font: inherit; min-width: 0; }
*, *::before, *::after { box-sizing: border-box; }
.dictionary-root {
    --font-size-no-units: 14;
    --line-height: 1.4;
    --list-padding1: 1.25em;
    --list-padding2: 1.75em;
    --compact-list-separator: "; ";
    --text-color: inherit;
    --text-color-light2: color-mix(in srgb, currentColor 50%, transparent);
    --text-color-light3: color-mix(in srgb, currentColor 65%, transparent);
    --text-color-light4: color-mix(in srgb, currentColor 78%, transparent);
    --background-color-dark1: color-mix(in srgb, currentColor 8%, transparent);
    --notification-background-color-lighter: color-mix(in srgb, currentColor 12%, transparent);
    --checkbox-disabled-color: color-mix(in srgb, currentColor 45%, transparent);
    --accent-color: #4da3ff;
    --accent-color-dark: #2378ce;
    --link-color: #4da3ff;
    overflow-wrap: anywhere;
    line-height: var(--line-height);
}
.structured-content { white-space: normal; }
.sense-item + .sense-item { margin-top: .45em; padding-top: .35em; border-top: 1px solid color-mix(in srgb, currentColor 18%, transparent); }
.gloss-sc-table-container { display: block; max-width: 100%; overflow-x: auto; }
.gloss-sc-table { border-collapse: collapse; table-layout: auto; }
.gloss-sc-thead, .gloss-sc-tfoot, .gloss-sc-th { font-weight: bold; background: var(--background-color-dark1); }
.gloss-sc-th, .gloss-sc-td { border: 1px solid var(--text-color-light2); padding: .25em; vertical-align: top; }
.gloss-sc-ol, .gloss-sc-ul { padding-left: var(--list-padding2); }
.gloss-sc-details { padding-left: var(--list-padding1); }
.gloss-sc-summary { cursor: pointer; list-style-position: outside; }
.gloss-link { color: var(--link-color); text-decoration: underline; cursor: pointer; }
.gloss-image-link { color: var(--accent-color); display: inline-block; max-width: 100%; vertical-align: top; }
.gloss-image-container { display: inline-block; max-width: 100%; max-height: 70vh; overflow: hidden; vertical-align: top; }
.gloss-image { display: block; max-width: 100%; height: auto; object-fit: contain; }
.gloss-image-link[data-image-rendering="pixelated"] .gloss-image { image-rendering: pixelated; }
.gloss-image-link[data-image-rendering="crisp-edges"] .gloss-image { image-rendering: crisp-edges; }
.gloss-image-link[data-appearance="monochrome"] .gloss-image { filter: grayscale(1); }
.gloss-image-link-text { display: none; }
.gloss-image-description { display: block; white-space: pre-line; }
.gloss-image-link[data-collapsed="true"] .gloss-image-container { display: none; }
.gloss-image-link[data-collapsed="true"] .gloss-image-link-text { display: inline; }
.gloss-image-link[data-collapsed="true"]:hover .gloss-image-container { display: block; }
.gloss-image-missing { display: inline-block; padding: .4em; opacity: .6; border: 1px dashed currentColor; }
'''
