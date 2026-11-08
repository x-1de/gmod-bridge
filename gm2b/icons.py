"""Иконки интерфейса: SVG в стиле контурных иконок (24×24, обводка 2px), красятся в нужный цвет."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "import": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "addon": '<path d="m21 8-9-5-9 5 9 5 9-5Z"/><path d="m3 8v8l9 5 9-5V8"/><path d="M12 13v8"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 '
                '2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 '
                '1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 '
                '0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 '
                '1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 '
                '1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>',
    "cube": '<path d="m12 2 9 5v10l-9 5-9-5V7Z"/><path d="m3 7 9 5 9-5"/><path d="M12 12v10"/>',
    "map": '<path d="m9 4-6 3v13l6-3 6 3 6-3V4l-6 3-6-3Z"/><path d="M9 4v13"/><path d="M15 7v13"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "terminal": '<path d="m5 8 4 4-4 4"/><path d="M12 17h7"/>',
    "chevron": '<path d="m6 9 6 6 6-6"/>',
    "upload": '<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M5 21h14"/>',
    "play": '<path d="M7 4v16l13-8Z"/>',
    "plug": '<path d="M9 2v6"/><path d="M15 2v6"/><path d="M6 8h12v4a6 6 0 0 1-12 0Z"/><path d="M12 18v4"/>',
    "workshop": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18"/>'
                '<path d="M12 3a14 14 0 0 0 0 18"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
             '<path d="m21 15-5-5L5 21"/>',
    "game": '<rect x="2" y="6" width="20" height="12" rx="4"/><path d="M6 12h4"/><path d="M8 10v4"/>'
            '<circle cx="15" cy="11" r="1"/><circle cx="18" cy="13" r="1"/>',
    "person": '<circle cx="12" cy="6" r="3"/><path d="M12 9v7"/><path d="m6 11 6 1 6-1"/>'
              '<path d="m9 21 3-5 3 5"/>',
    "bone": '<path d="M7 7 17 17"/><circle cx="6" cy="5" r="2"/><circle cx="5" cy="8" r="2"/>'
            '<circle cx="18" cy="16" r="2"/><circle cx="16" cy="19" r="2"/>',
    "back": '<path d="m15 18-6-6 6-6"/>',
}

_cache: dict[tuple, QPixmap] = {}


def pixmap(name: str, color: str = "#c9ced8", size: int = 18, ratio: float = 2.0) -> QPixmap:
    key = (name, color, size, ratio)
    if key not in _cache:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
               f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>')
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        pm = QPixmap(QSize(int(size * ratio), int(size * ratio)))
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        pm.setDevicePixelRatio(ratio)
        _cache[key] = pm
    return _cache[key]


def icon(name: str, color: str = "#c9ced8", active: str | None = None, size: int = 18) -> QIcon:
    """Иконка; active — цвет для выбранного/нажатого состояния (навигация, чипы)."""
    ic = QIcon(pixmap(name, color, size))
    if active:
        ic.addPixmap(pixmap(name, active, size), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.On)
    return ic
