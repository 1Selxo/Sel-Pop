from __future__ import annotations

import base64
import mimetypes
import re
from html import escape
from typing import Any, Callable, Dict, List

MediaLoader = Callable[[str], bytes | None]

_CONTAINER_TAGS = {
    'ruby', 'rt', 'rp', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
    'span', 'div', 'ol', 'ul', 'li', 'details', 'summary',
}
_STYLED_TAGS = {'span', 'div', 'ol', 'ul', 'li', 'details', 'summary', 'th', 'td'}
_STYLE_PROPERTIES = {
    'fontStyle': 'font-style',
    'fontWeight': 'font-weight',
    'fontSize': 'font-size',
    'color': 'color',
    'background': 'background',
    'backgroundColor': 'background-color',
    'textDecorationLine': 'text-decoration-line',
    'textDecorationStyle': 'text-decoration-style',
    'textDecorationColor': 'text-decoration-color',
    'borderColor': 'border-color',
    'borderStyle': 'border-style',
    'borderRadius': 'border-radius',
    'borderWidth': 'border-width',
    'clipPath': 'clip-path',
    'verticalAlign': 'vertical-align',
    'textAlign': 'text-align',
    'textEmphasis': 'text-emphasis',
    'textShadow': 'text-shadow',
    'margin': 'margin',
    'marginTop': 'margin-top',
    'marginLeft': 'margin-left',
    'marginRight': 'margin-right',
    'marginBottom': 'margin-bottom',
    'padding': 'padding',
    'paddingTop': 'padding-top',
    'paddingLeft': 'padding-left',
    'paddingRight': 'padding-right',
    'paddingBottom': 'padding-bottom',
    'wordBreak': 'word-break',
    'whiteSpace': 'white-space',
    'cursor': 'cursor',
    'listStyleType': 'list-style-type',
}
_NUMERIC_EM_PROPERTIES = {'marginTop', 'marginLeft', 'marginRight', 'marginBottom'}
_SAFE_CSS_VALUE_RE = re.compile(r'^[^{};<>]*$')


def _data_name(key: str) -> str | None:
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', key) is None:
        return None
    kebab = re.sub(r'(?<!^)(?=[A-Z])', '-', key).replace('_', '-').lower()
    return f'data-sc-{kebab}'


def _render_style(style: Any) -> str:
    if not isinstance(style, dict):
        return ''
    declarations = []
    for source_name, css_name in _STYLE_PROPERTIES.items():
        value = style.get(source_name)
        if source_name == 'textDecorationLine' and isinstance(value, list):
            value = ' '.join(str(item) for item in value)
        if source_name in _NUMERIC_EM_PROPERTIES and isinstance(value, (int, float)):
            value = f'{value}em'
        if not isinstance(value, str) or not value or not _SAFE_CSS_VALUE_RE.match(value):
            continue
        declarations.append(f'{css_name}:{value}')
    return ';'.join(declarations)


def _render_attributes(node: dict[str, Any], styled: bool = False) -> str:
    attributes = [f'class="gloss-sc-{escape(str(node.get("tag") or ""), quote=True)}"']
    data = node.get('data')
    if isinstance(data, dict):
        for key, value in data.items():
            if key and isinstance(value, str):
                name = _data_name(str(key))
                if name is not None:
                    attributes.append(f'{name}="{escape(value, quote=True)}"')
    lang = node.get('lang')
    if isinstance(lang, str) and lang:
        attributes.append(f'lang="{escape(lang, quote=True)}"')
    if styled:
        style = _render_style(node.get('style'))
        if style:
            attributes.append(f'style="{escape(style, quote=True)}"')
        title = node.get('title')
        if isinstance(title, str) and title:
            attributes.append(f'title="{escape(title, quote=True)}"')
        if node.get('open') is True and node.get('tag') == 'details':
            attributes.append('open')
    if node.get('tag') in {'td', 'th'}:
        for source_name, html_name in (('colSpan', 'colspan'), ('rowSpan', 'rowspan')):
            value = node.get(source_name)
            if isinstance(value, int) and value > 0:
                attributes.append(f'{html_name}="{value}"')
    return ' '.join(attributes)


def _render_image(node: dict[str, Any], media_loader: MediaLoader | None) -> str:
    path = str(node.get('path') or '')
    media = media_loader(path) if path and media_loader is not None else None
    mime_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    src = ''
    state = 'unloaded'
    if media:
        src = f'data:{mime_type};base64,{base64.b64encode(media).decode("ascii")}'
        state = 'loaded'

    width = node.get('preferredWidth', node.get('width', 100))
    height = node.get('preferredHeight', node.get('height', 100))
    width = width if isinstance(width, (int, float)) and width > 0 else 100
    height = height if isinstance(height, (int, float)) and height > 0 else 100
    units = node.get('sizeUnits') if node.get('sizeUnits') in {'px', 'em'} else 'px'
    rendering = node.get('imageRendering') or ('pixelated' if node.get('pixelated') else 'auto')
    appearance = node.get('appearance') if node.get('appearance') in {'auto', 'monochrome'} else 'auto'
    alt = escape(str(node.get('alt') or node.get('description') or 'Image'), quote=True)
    title = escape(str(node.get('title') or ''), quote=True)
    collapsed = str(bool(node.get('collapsed'))).lower()
    collapsible = str(node.get('collapsible', True)).lower()
    image = (
        f'<img class="gloss-image" src="{src}" alt="{alt}" '
        f'width="{width}" height="{height}" loading="lazy">'
        if src else '<span class="gloss-image-missing">Image unavailable</span>'
    )
    description = node.get('description')
    description_html = (
        f'<span class="gloss-image-description">{escape(description)}</span>'
        if isinstance(description, str) and description else ''
    )
    return (
        f'<a class="gloss-image-link" data-path="{escape(path, quote=True)}" '
        f'data-image-load-state="{state}" data-image-rendering="{escape(str(rendering), quote=True)}" '
        f'data-appearance="{appearance}" data-collapsed="{collapsed}" '
        f'data-collapsible="{collapsible}" data-size-units="{units}">'
        f'<span class="gloss-image-container" title="{title}" '
        f'style="width:{width}{units};max-width:100%;">{image}</span>'
        f'<span class="gloss-image-link-text">Image</span></a>{description_html}'
    )


def render_node(node: Any, media_loader: MediaLoader | None = None) -> str:
    """Render Yomitan structured content into safe, browser-ready HTML."""
    if isinstance(node, str):
        return escape(node)
    if isinstance(node, list):
        return ''.join(render_node(child, media_loader) for child in node)
    if not isinstance(node, dict):
        return ''

    tag = node.get('tag')
    if tag == 'br':
        return f'<br {_render_attributes(node)}>'
    if tag == 'img':
        return _render_image(node, media_loader)
    if tag == 'a':
        href = str(node.get('href') or '')
        inner = render_node(node.get('content'), media_loader)
        external = not href.startswith('?')
        safe_href = href if external and re.match(r'^https?://', href, re.IGNORECASE) else '#'
        return (
            f'<a class="gloss-link" href="{escape(safe_href, quote=True)}" '
            f'data-external="{str(external).lower()}"><span class="gloss-link-text">{inner}</span></a>'
        )
    if tag not in _CONTAINER_TAGS:
        return render_node(node.get('content'), media_loader)

    inner = render_node(node.get('content'), media_loader)
    attributes = _render_attributes(node, styled=tag in _STYLED_TAGS)
    element = f'<{tag} {attributes}>{inner}</{tag}>'
    if tag == 'table':
        return f'<div class="gloss-sc-table-container">{element}</div>'
    return element


def handle_structured_content(
    item: Dict[str, Any],
    media_loader: MediaLoader | None = None,
) -> List[str]:
    content = item.get('content')
    html_output = render_node(content, media_loader)
    return [f'<div class="structured-content">{html_output}</div>'] if html_output else []
