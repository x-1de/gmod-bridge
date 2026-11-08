"""Главное окно: боковая навигация, три раздела и общий журнал внизу."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStackedWidget, QVBoxLayout,
    QWidget,
)

from . import __version__
from .addon_builder import ADDON_TAGS, INPUT_EXTENSIONS, SURFACES, AddonOptions, build_addon
from .addon_install import addon_installed, install_addon
from .bsp import Bsp
from .exporter import ExportOptions, export_model, find_blender
from .icons import icon, pixmap
from .map_exporter import export_map
from .materials import read_vtf, resolve_material, resolve_world_material
from .sources import (
    FolderSource, GmaSource, Source, VirtualFS, ZipSource, find_gmod_dir, find_workshop_addons,
    game_sources,
)
from .studio import SourceModel
from .theme import ACCENT, BAD, MUTED, OK, QSS

APP_NAME = "GMod Bridge"
ROLE_DATA = Qt.ItemDataRole.UserRole

MODEL_STATS = [("bones", "Кости"), ("triangles", "Треугольники"), ("vertices", "Вершины"),
               ("bodygroups", "Bodygroups"), ("skins", "Скины"), ("flexes", "Shape keys"),
               ("version", "Версия MDL"), ("materials", "Материалы")]
MAP_STATS = [("faces", "Грани"), ("props", "Пропы"), ("unique", "Разных моделей"), ("lights", "Свет"),
             ("disp", "Displacement"), ("brushes", "Двери/браши"), ("version", "Версия BSP"),
             ("textures", "Текстуры")]
FORMATS = [("blend", "Blender", "Всё: кости, скины, лицо, драйверы — для работы в Blender"),
           ("fbx", "FBX", "Кости, веса, shape keys, текстуры внутри — Unity, Unreal, Maya, 3ds Max"),
           ("glb", "glTF", "GLB: кости, веса, shape keys, текстуры внутри — Godot, веб, Sketchfab"),
           ("obj", "OBJ", "Только геометрия и материалы, без костей — универсальный формат")]


# ------------------------------------------------------------------ мелкие виджеты

def card(name: str = "Card", margins: int = 16) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(objectName=name)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(10)
    return frame, lay


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def icon_label(name: str, color: str = MUTED, size: int = 18) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(pixmap(name, color, size))
    return lbl


def page_header(title: str, subtitle: str) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(2)
    box.addWidget(label(title, "PageTitle"))
    box.addWidget(label(subtitle, "PageSub"))
    return box


def section(title: str, icon_name: str | None = None, right: QWidget | None = None) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    if icon_name:
        row.addWidget(icon_label(icon_name, ACCENT, 17))
    row.addWidget(label(title, "Section"))
    row.addStretch()
    if right:
        row.addWidget(right)
    return row


def open_folder(path: Path):
    if path and Path(path).exists():
        os.startfile(str(path)) if os.name == "nt" else None


# ------------------------------------------------------------------ фоновые задачи

class Worker(QObject):
    """Живёт в фоновом потоке и выполняет fn(worker)."""
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    @Slot()
    def run(self):
        try:
            self.done.emit(self.fn(self))
        except Exception as e:  # noqa: BLE001 — показываем пользователю любую ошибку
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class Task(QObject):
    """Живёт в главном потоке: принимает результат Worker и вызывает колбэки уже в UI-потоке."""

    def __init__(self, parent, fn, on_done, on_error):
        super().__init__(parent)
        self.on_done, self.on_error = on_done, on_error
        self.thread = QThread(self)
        self.worker = Worker(fn)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)

    def _finish(self):
        self.thread.quit()
        self.thread.wait()

    @Slot(object)
    def _done(self, result):
        self._finish()
        self.on_done(result)

    @Slot(str)
    def _failed(self, error):
        self._finish()
        self.on_error(error)


# ================================================================== Из GMod

class ImportPage(QWidget):
    """Аддоны GMod -> модели и карты -> Blender / FBX / glTF / OBJ."""

    def __init__(self, win: "MainWindow"):
        super().__init__(objectName="Page")
        self.win = win
        self.addons: list[Source] = []
        self.current: SourceModel | None = None
        self._material_infos = []
        self._map_cache: dict[str, Bsp] = {}
        self._preview_vfs: VirtualFS | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 18)
        lay.setSpacing(16)
        lay.addLayout(page_header("Из Garry's Mod",
                                  "Модели и карты из аддонов — с костями, текстурами, скинами и мимикой"))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._build_addons())
        split.addWidget(self._build_models())
        split.addWidget(self._build_details())
        split.setSizes([280, 380, 560])
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        lay.addWidget(self._build_export())

    # ---------------------------------------------------------------- колонки

    def _build_addons(self):
        frame, lay = card()
        lay.addLayout(section("Аддоны", "addon"))
        btns = QHBoxLayout()
        b_file = QPushButton(icon("file"), " Файл .gma")
        b_file.clicked.connect(self.open_file_dialog)
        b_dir = QPushButton(icon("folder"), " Папка")
        b_dir.clicked.connect(self.open_folder_dialog)
        btns.addWidget(b_file)
        btns.addWidget(b_dir)
        lay.addLayout(btns)
        b_ws = QPushButton(icon("workshop"), " Скачанные из Workshop")
        b_ws.clicked.connect(self.scan_workshop)
        lay.addWidget(b_ws)
        self.addon_filter = QLineEdit(placeholderText="Поиск аддона")
        self.addon_filter.addAction(icon("search", MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.addon_filter.textChanged.connect(self._filter_addons)
        lay.addWidget(self.addon_filter)
        self.addon_list = QListWidget()
        self.addon_list.setIconSize(QSize(18, 18))
        self.addon_list.currentItemChanged.connect(self._addon_selected)
        lay.addWidget(self.addon_list, 1)
        lay.addWidget(label("Можно перетащить .gma или папку прямо в окно", "Hint", wrap=True))
        return frame

    def _build_models(self):
        frame, lay = card()
        self.model_count = label("", "Badge")
        self.model_count.hide()
        lay.addLayout(section("Модели и карты", "cube", self.model_count))
        self.model_filter = QLineEdit(placeholderText="Фильтр: player, weapon, props…")
        self.model_filter.addAction(icon("search", MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.model_filter.textChanged.connect(self._filter_models)
        lay.addWidget(self.model_filter)
        self.model_list = QListWidget()
        self.model_list.setIconSize(QSize(16, 16))
        self.model_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.model_list.currentItemChanged.connect(self._model_selected)
        self.model_list.itemSelectionChanged.connect(self.update_export_button)
        lay.addWidget(self.model_list, 1)
        lay.addWidget(label("Ctrl / Shift — выбрать несколько для пакетного экспорта", "Hint", wrap=True))
        return frame

    def _build_details(self):
        frame, lay = card()
        self.d_kind = label("", "Badge")
        self.d_kind.hide()
        head = QHBoxLayout()
        self.d_name = label("Выберите модель или карту", "ModelName")
        head.addWidget(self.d_name, 1)
        head.addWidget(self.d_kind)
        lay.addLayout(head)
        self.d_path = label("", "Muted", wrap=True)
        lay.addWidget(self.d_path)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.stat_cells: list[tuple[QLabel, QLabel]] = []
        for i in range(len(MODEL_STATS)):
            cell, cl = card("Stat", 10)
            cl.setSpacing(0)
            value, title = label("—", "StatValue"), label(MODEL_STATS[i][1], "StatTitle")
            cl.addWidget(value)
            cl.addWidget(title)
            self.stat_cells.append((value, title))
            grid.addWidget(cell, i // 4, i % 4)
        lay.addLayout(grid)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.preview = label("нет превью", "Preview")
        self.preview.setFixedSize(190, 190)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        mats = QVBoxLayout()
        self.skin_box = QComboBox()
        self.skin_box.setToolTip("Какой скин показать и включить по умолчанию.\n"
                                 "В Blender скин переключается свойством «skin» у арматуры.")
        self.skin_box.currentIndexChanged.connect(self._show_materials)
        self.skin_label = label("Скин", "Muted")
        skin_row = QWidget()
        sr = QHBoxLayout(skin_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(self.skin_label)
        sr.addWidget(self.skin_box)
        mats.addLayout(section("Материалы", "image", skin_row))
        self.mat_list = QListWidget()
        self.mat_list.currentItemChanged.connect(self._material_selected)
        mats.addWidget(self.mat_list)
        body.addLayout(mats, 1)
        lay.addLayout(body, 1)
        return frame

    def _build_export(self):
        frame, lay = card(margins=14)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("Формат", "Muted"))
        saved = (self.win.settings.value("formats", "blend") or "blend").split(",")
        self.format_buttons: dict[str, QPushButton] = {}
        for key, title, tip in FORMATS:
            b = QPushButton(title, objectName="Chip", checkable=True)
            b.setChecked(key in saved)
            b.setToolTip(tip)
            b.toggled.connect(self.update_export_button)
            self.format_buttons[key] = b
            row.addWidget(b)
        row.addSpacing(16)
        row.addWidget(label("Масштаб", "Muted"))
        self.scale_box = QComboBox()
        self.scale_box.addItem("Метры", 0.0254)
        self.scale_box.addItem("Юниты Source", 1.0)
        self.scale_box.setToolTip("Метры: 1 дюйм Source = 0.0254 м (реальный размер в Blender)")
        row.addWidget(self.scale_box)
        self.more_btn = QPushButton(icon("chevron", MUTED), " Ещё", objectName="Ghost", checkable=True)
        row.addWidget(self.more_btn)
        row.addStretch()
        self.export_btn = QPushButton(icon("play", "#ffffff"), "  Экспортировать", objectName="Primary")
        self.export_btn.setEnabled(False)
        self.export_btn.setMinimumWidth(230)
        self.export_btn.clicked.connect(self.export_selected)
        row.addWidget(self.export_btn)
        lay.addLayout(row)

        self.more = QWidget()
        more = QHBoxLayout(self.more)
        more.setContentsMargins(0, 4, 0, 0)
        more.setSpacing(18)
        self.pack_box = QCheckBox("Упаковать текстуры в .blend")
        self.pack_box.setChecked(True)
        self.open_box = QCheckBox("Открыть в Blender после экспорта")
        self.open_box.setChecked(True)
        self.tex_box = QComboBox()
        for text, size in (("оригинал", 0), ("до 2048", 2048), ("до 1024", 1024), ("до 512", 512)):
            self.tex_box.addItem(text, size)
        self.tex_box.setCurrentIndex(2)
        self.tex_box.setToolTip("Blender хранит текстуры в видеопамяти без сжатия.\n"
                                "Большая карта в оригинале может занять 8–9 ГБ — тогда вьюпорт тормозит.")
        self.lights_box = QCheckBox("Свет карты")
        self.lights_box.setChecked(True)
        self.lights_box.setToolTip("Лампы карты создаются без теней, чтобы режим Rendered не тормозил.")
        more.addWidget(self.pack_box)
        more.addWidget(self.open_box)
        tex = QHBoxLayout()
        tex.setSpacing(8)
        tex.addWidget(label("Текстуры карт", "Muted"))
        tex.addWidget(self.tex_box)
        more.addLayout(tex)
        more.addWidget(self.lights_box)
        more.addStretch()
        self.more.hide()
        self.more_btn.toggled.connect(self.more.setVisible)
        lay.addWidget(self.more)
        return frame

    # ---------------------------------------------------------------- состояние

    def formats(self) -> list[str]:
        return [k for k, b in self.format_buttons.items() if b.isChecked()]

    def update_export_button(self, *_):
        n = len(self.model_list.selectedItems())
        fmts = self.formats()
        self.export_btn.setEnabled(n > 0 and bool(fmts) and self.win.blender_ok())
        names = " + ".join(t for k, t, _ in FORMATS if k in fmts) or "выберите формат"
        self.export_btn.setText(f"  Экспорт → {names}" + (f"  ({n})" if n > 1 else ""))

    def vfs_for(self, addon: Source) -> VirtualFS:
        """Где искать файлы: сам аддон, остальные открытые, весь скачанный Workshop, игра.
        Карты и модели часто ссылаются на контент из других аддонов того же сервера."""
        others = [a for a in self.addons if a is not addon]
        opened = {getattr(a, "path", None) for a in self.addons}
        content = [c for c in self.win.content if c.path not in opened]
        return VirtualFS([addon, *others, *content, *self.win.game])

    def current_addon(self) -> Source | None:
        item = self.addon_list.currentItem()
        return item.data(ROLE_DATA) if item else None

    def content_loaded(self):
        item = self.model_list.currentItem()
        if item:
            self._model_selected(item)  # перепроверить материалы с новым контентом

    # ---------------------------------------------------------------- аддоны

    def open_file_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Открыть аддон", "", "Аддоны GMod (*.gma *.bin)")
        for f in files:
            self.add_addon(f)

    def open_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Папка распакованного аддона")
        if d:
            self.add_addon(d)

    def add_addon(self, path: str, select: bool = True) -> Source | None:
        p = Path(path)
        try:
            src = FolderSource(p) if p.is_dir() else GmaSource(p)
        except Exception as e:  # noqa: BLE001
            self.win.log(f"✖ {p.name}: {e}")
            return None
        if not src.models() and not src.maps():
            self.win.log(f"• {src.title}: нет ни моделей, ни карт — пропущено")
            return None
        item = self._add_addon_item(src)
        if select:
            self.addon_list.setCurrentItem(item)
        return src

    def _add_addon_item(self, src: Source) -> QListWidgetItem:
        self.addons.append(src)
        author = getattr(src, "author", "")
        parts = []
        if src.maps():
            parts.append(f"{len(src.maps())} карт")
        if src.models():
            parts.append(f"{len(src.models())} моделей")
        if author and author != "Author Name":
            parts.append(author)
        item = QListWidgetItem(icon("map" if src.maps() else "addon", "#9aa1b0"),
                               f"{src.title}\n{' · '.join(parts)}")
        item.setData(ROLE_DATA, src)
        item.setToolTip(f"{src.subtitle}\n\n{getattr(src, 'description', '')}".strip())
        self.addon_list.addItem(item)
        return item

    def scan_workshop(self):
        paths = find_workshop_addons(self.win.gmod_dir)
        if not paths:
            QMessageBox.information(self, "Workshop", "Скачанные аддоны не найдены.")
            return
        known = {getattr(a, "path", None) for a in self.addons}
        paths = [p for p in paths if str(p) not in known]
        loaded = {c.path: c for c in self.win.content}
        self.win.log(f"Сканирую {len(paths)} аддонов…")

        def job(w: Worker):
            found = []
            for i, p in enumerate(paths):
                w.progress.emit(i + 1, len(paths))
                try:
                    src = loaded.get(str(p)) or GmaSource(p)
                    if src.models() or src.maps():
                        found.append(src)
                except Exception:  # noqa: BLE001 — битые аддоны просто пропускаем
                    pass
            return found

        def done(found):
            for src in found:
                self._add_addon_item(src)
            self.win.log(f"Найдено аддонов с моделями или картами: {len(found)}")

        self.win.run_bg(job, done)

    def _filter_addons(self, text):
        t = text.lower()
        for i in range(self.addon_list.count()):
            it = self.addon_list.item(i)
            it.setHidden(t not in it.text().lower())

    def _addon_selected(self, item):
        self.model_list.clear()
        if not item:
            return
        src: Source = item.data(ROLE_DATA)
        maps, models = src.maps(), src.models()
        for m in maps:  # карты — первыми
            it = QListWidgetItem(icon("map", ACCENT), m[len("maps/"):])
            it.setData(ROLE_DATA, m)
            it.setToolTip(f"{m}\nКарта: мир, пропы, двери и свет")
            self.model_list.addItem(it)
        for m in models:
            # две строки: имя модели крупно, папка под ним — длинные пути не обрезают имя
            rel = Path(m[len("models/"):])
            it = QListWidgetItem(icon("cube", "#7d8494"), f"{rel.stem}\n{rel.parent.as_posix()}")
            it.setData(ROLE_DATA, m)
            it.setToolTip(m)
            self.model_list.addItem(it)
        self.model_count.setText((f"{len(maps)} карт · " if maps else "") + f"{len(models)} моделей")
        self.model_count.show()
        self._filter_models(self.model_filter.text())
        if maps or models:
            self.model_list.setCurrentRow(0)

    def _filter_models(self, text):
        t = text.lower()
        for i in range(self.model_list.count()):
            it = self.model_list.item(i)
            hidden = t not in it.text().lower()
            it.setHidden(hidden)
            if hidden:
                it.setSelected(False)  # иначе скрытая модель уйдёт в пакетный экспорт
        self.update_export_button()

    # ---------------------------------------------------------------- детали

    def _show_stats(self, layout, values: dict | None):
        for (value_lbl, title_lbl), (key, title) in zip(self.stat_cells, layout):
            title_lbl.setText(title)
            v = values.get(key) if values else None
            value_lbl.setText("—" if v is None else f"{v:,}".replace(",", " "))

    def _model_selected(self, item):
        self.mat_list.clear()
        self.preview.setPixmap(QPixmap())
        self.preview.setText("нет превью")
        addon = self.current_addon()
        if not item or not addon:
            return
        path = item.data(ROLE_DATA)
        is_map = path.endswith(".bsp")
        self.d_name.setText(Path(path).stem)
        self.d_path.setText(path)
        self.d_kind.setText("Карта" if is_map else "Модель")
        self.d_kind.show()
        vfs = self._preview_vfs = self.vfs_for(addon)
        self.skin_label.setVisible(not is_map)
        self.skin_box.setVisible(not is_map)
        if is_map:
            self._map_selected(vfs, path)
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.current = SourceModel.load(vfs, path)
            self._material_infos = [resolve_material(vfs, self.current.mdl, i)
                                    for i in range(len(self.current.mdl.textures))]
            self._show_stats(MODEL_STATS, {**self.current.stats(), "materials": len(self._material_infos)})
            self.skin_box.blockSignals(True)
            self.skin_box.clear()
            self.skin_box.addItems([f"скин {i}" for i in range(len(self.current.mdl.skins))])
            many = len(self.current.mdl.skins) > 1
            self.skin_box.setVisible(many)
            self.skin_label.setVisible(many)
            self.skin_box.blockSignals(False)
            self._show_materials()
        except Exception as e:  # noqa: BLE001
            self.current = None
            self._show_stats(MODEL_STATS, None)
            self.win.log(f"✖ {path}: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _map_selected(self, vfs: VirtualFS, path: str):
        """Карта: статистика BSP и список текстур брашей (с учётом pakfile карты)."""
        self.current = None
        self.skin_box.clear()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if path not in self._map_cache:
                self._map_cache[path] = Bsp(vfs.read(path))
            bsp = self._map_cache[path]
            props = bsp.static_props() + bsp.entity_props()
            names = sorted({t for t in bsp.texture_names if not t.startswith("tools/")})
            self._show_stats(MAP_STATS, {
                "faces": len(bsp.faces), "props": len(props), "unique": len({p.model for p in props}),
                "lights": len(bsp.lights()), "disp": len(bsp.dispinfo) // 176,
                "brushes": len(bsp.brush_entities()), "version": bsp.version, "textures": len(names),
            })
            vfs = self._preview_vfs = VirtualFS([ZipSource(bsp.pakfile), *vfs.sources])
            first = None
            for name in names:
                info = resolve_world_material(vfs, name)
                it = QListWidgetItem(icon("check" if info.found else "x", OK if info.found else BAD, size=14), name)
                it.setToolTip(f"{info.vmt_path or 'VMT не найден'}\nшейдер: {info.shader}\n"
                              f"текстура: {info.base_texture or '—'}")
                it.setData(ROLE_DATA, info.base_texture)
                self.mat_list.addItem(it)
                if first is None and info.base_texture:
                    first = self.mat_list.count() - 1
            if first is not None:
                self.mat_list.setCurrentRow(first)
        except Exception as e:  # noqa: BLE001
            self._show_stats(MAP_STATS, None)
            self.win.log(f"✖ {path}: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _show_materials(self):
        """Материалы выбранного скина: слот -> текстура из таблицы скинов."""
        self.mat_list.clear()
        if not self.current:
            return
        family = self.current.mdl.skins[max(self.skin_box.currentIndex(), 0)]
        base_family = self.current.mdl.skins[0]
        first_preview = first_changed = None
        for slot, tex in enumerate(family):
            if tex >= len(self._material_infos):
                continue
            info = self._material_infos[tex]
            changed = tex != base_family[slot]
            it = QListWidgetItem(icon("check" if info.found else "x", OK if info.found else BAD, size=14),
                                 info.name.split("/")[-1] + ("   ⇄ этот скин" if changed else ""))
            if changed:
                it.setForeground(QBrush(QColor("#ffc79f")))
            it.setToolTip(f"{info.vmt_path or 'VMT не найден'}\nшейдер: {info.shader}\n"
                          f"текстура: {info.base_texture or '—'}\nнормали: {info.normal_texture or '—'}")
            it.setData(ROLE_DATA, info.base_texture)
            self.mat_list.addItem(it)
            row = self.mat_list.count() - 1
            if info.base_texture and first_preview is None:
                first_preview = row
            if info.base_texture and changed and first_changed is None:
                first_changed = row
        # Превью: первый слот, который этот скин меняет, иначе первый с текстурой
        row = first_changed if first_changed is not None else first_preview
        if row is not None:
            self.mat_list.setCurrentRow(row)

    def _material_selected(self, item):
        if not item or not item.data(ROLE_DATA) or self._preview_vfs is None:
            return
        try:
            img = read_vtf(self._preview_vfs.read(item.data(ROLE_DATA))).convert("RGBA")
            img.thumbnail((186, 186))
            qimg = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
            self.preview.setPixmap(QPixmap.fromImage(qimg.copy()))
        except Exception as e:  # noqa: BLE001
            self.preview.setText(f"не удалось\nпрочитать VTF\n{e}")

    # ---------------------------------------------------------------- экспорт

    def export_selected(self):
        addon = self.current_addon()
        paths = [it.data(ROLE_DATA) for it in self.model_list.selectedItems()]
        if not addon or not paths:
            return
        self.win.settings.setValue("formats", ",".join(self.formats()))
        opts = ExportOptions(
            out_dir=self.win.out_dir(),
            blender_exe=self.win.blender_exe(),
            scale=self.scale_box.currentData(),
            # выбранный скин применяется только при экспорте одной модели
            skin=max(self.skin_box.currentIndex(), 0) if len(paths) == 1 else 0,
            pack_textures=self.pack_box.isChecked(),
            open_after=self.open_box.isChecked() and len(paths) == 1,
            max_texture=self.tex_box.currentData(),
            formats=tuple(self.formats()),
        )
        vfs = self.vfs_for(addon)
        lights = self.lights_box.isChecked()

        def job(w: Worker):
            ok, failed = [], []
            for i, p in enumerate(paths):
                w.progress.emit(i, len(paths))
                try:
                    if p.endswith(".bsp"):
                        ok.append(export_map(vfs, p, opts, w.log.emit, import_lights=lights))
                    else:
                        ok.append(export_model(vfs, p, opts, w.log.emit))
                except Exception as e:  # noqa: BLE001
                    w.log.emit(f"✖ {p}: {e}")
                    failed.append(p)
            w.progress.emit(len(paths), len(paths))
            return ok, failed

        def done(result):
            ok, failed = result
            self.update_export_button()
            msg = f"Готово: {len(ok)}" + (f", с ошибками: {len(failed)}" if failed else "")
            self.win.log(msg)
            if ok and not opts.open_after:
                self.win.notify("Экспорт завершён", f"{msg}\nФайлы: {opts.out_dir}",
                                ok[0] if len(ok) == 1 else Path(opts.out_dir))

        self.export_btn.setEnabled(False)
        self.win.run_bg(job, done)


# ================================================================== В GMod

class AddonPage(QWidget):
    """Своя модель -> готовый аддон Garry's Mod."""

    def __init__(self, win: "MainWindow"):
        super().__init__(objectName="Page")
        self.win = win
        self.input: Path | None = None
        self.result = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 18)
        lay.setSpacing(16)
        lay.addLayout(page_header("В Garry's Mod",
                                  "Своя модель из Blender, FBX, OBJ или glTF → готовый аддон с физикой"))
        row = QHBoxLayout()
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(self._build_drop(), 1)
        left.addWidget(self._build_result())
        row.addLayout(left, 5)
        row.addWidget(self._build_form(), 6)
        lay.addLayout(row, 1)

    def _build_drop(self):
        self.drop, lay = card("Drop", 24)
        lay.addStretch()
        self.drop_icon = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.drop_icon.setPixmap(pixmap("upload", ACCENT, 46))
        lay.addWidget(self.drop_icon)
        self.drop_title = label("Перетащите модель сюда", "ModelName")
        self.drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.drop_title)
        self.drop_sub = label("FBX · OBJ · glTF / GLB · BLEND · STL · PLY", "Muted")
        self.drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.drop_sub)
        btn = QPushButton(icon("folder"), " Выбрать файл")
        btn.clicked.connect(self.pick_file)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch()
        return self.drop

    def _build_result(self):
        self.result_card, lay = card("Result")
        head = QHBoxLayout()
        head.addWidget(icon_label("check", OK, 20))
        self.result_title = label("Аддон готов", "Section")
        head.addWidget(self.result_title)
        head.addStretch()
        lay.addLayout(head)
        self.result_text = label("", "Muted", wrap=True)
        self.result_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.result_text)
        btns = QHBoxLayout()
        b_open = QPushButton(icon("folder"), " Открыть папку")
        b_open.clicked.connect(lambda: open_folder(self.result.folder if self.result else None))
        self.check_btn = QPushButton(icon("cube"), " Проверить в Blender")
        self.check_btn.setToolTip("Открыть скомпилированную модель обратно в Blender — так видно, что получилось в игре")
        self.check_btn.clicked.connect(self.check_in_blender)
        btns.addWidget(b_open)
        btns.addWidget(self.check_btn)
        btns.addStretch()
        lay.addLayout(btns)
        self.result_card.hide()
        return self.result_card

    def _build_form(self):
        frame, lay = card(margins=20)
        lay.addLayout(section("Параметры аддона", "addon"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        self.title_edit = QLineEdit(placeholderText="Например: Деревянный ящик")
        self.author_edit = QLineEdit(self.win.settings.value("addon_author", "") or "gm2b")
        self.author_edit.setToolTip("Папка внутри models/ — чтобы ваши модели не смешивались с чужими")
        self.surface_box = QComboBox()
        for title, value in SURFACES.items():
            self.surface_box.addItem(title, value)
        self.mass_box = QDoubleSpinBox(decimals=1, minimum=0.1, maximum=50000, value=20, suffix=" кг")
        self.scale_box = QComboBox()
        self.scale_box.addItem("1 м в Blender = 1 м в игре (рекомендуется)", 1 / 0.0254)
        self.scale_box.addItem("Без пересчёта (1 юнит = 1 юнит)", 1.0)
        self.rotate_box = QComboBox()
        for deg in (0, 90, 180, 270):
            self.rotate_box.addItem(f"{deg}°", deg)
        self.tag_box = QComboBox()
        for t in ADDON_TAGS:
            self.tag_box.addItem(t, t)
        rows = [("Название", self.title_edit), ("Папка автора", self.author_edit),
                ("Материал (звук, удары)", self.surface_box), ("Масса", self.mass_box),
                ("Масштаб", self.scale_box), ("Поворот вокруг Z", self.rotate_box),
                ("Категория Workshop", self.tag_box)]
        for i, (title, w) in enumerate(rows):
            grid.addWidget(label(title, "Muted"), i, 0)
            grid.addWidget(w, i, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addSpacing(6)
        self.install_box = QCheckBox("Сразу установить в Garry's Mod (папка addons)")
        self.install_box.setChecked(bool(self.win.gmod_dir))
        self.install_box.setEnabled(bool(self.win.gmod_dir))
        self.gma_box = QCheckBox("Собрать .gma для загрузки в Workshop")
        self.gma_box.setChecked(True)
        lay.addWidget(self.install_box)
        lay.addWidget(self.gma_box)
        lay.addStretch()
        lay.addWidget(label("Модель станет физическим пропом с коллизией. В игре: меню спавна (Q) → "
                            "поиск по названию модели.", "Hint", wrap=True))
        self.build_btn = QPushButton(icon("addon", "#ffffff"), "  Собрать аддон", objectName="Primary")
        self.build_btn.clicked.connect(self.build)
        lay.addWidget(self.build_btn)
        self.update_button()
        self.title_edit.textChanged.connect(self.update_button)
        return frame

    # ---------------------------------------------------------------- действия

    def update_button(self, *_):
        ready = self.input is not None and bool(self.title_edit.text().strip())
        tools = self.win.blender_ok() and bool(self.win.gmod_dir)
        self.build_btn.setEnabled(ready and tools)
        if not tools:
            self.build_btn.setToolTip("Нужны Blender и установленный Garry's Mod (раздел «Настройки»)")

    def pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Модель", "",
                                           "3D-модели (" + " ".join(f"*{e}" for e in INPUT_EXTENSIONS) + ")")
        if f:
            self.set_input(f)

    def set_input(self, path: str):
        p = Path(path)
        if p.suffix.lower() not in INPUT_EXTENSIONS:
            self.win.log(f"✖ {p.name}: формат не поддерживается")
            return
        self.input = p
        self.drop_icon.setPixmap(pixmap("cube", ACCENT, 46))
        self.drop_title.setText(p.name)
        size = p.stat().st_size
        size_text = f"{size / 1e6:.1f} МБ" if size >= 1e6 else f"{max(1, round(size / 1e3))} КБ"
        self.drop_sub.setText(f"{p.suffix[1:].upper()} · {size_text} · папка «{p.parent.name}»")
        self.drop_sub.setToolTip(str(p))
        if not self.title_edit.text().strip():
            self.title_edit.setText(p.stem.replace("_", " ").title())
        self.update_button()

    def build(self):
        self.win.settings.setValue("addon_author", self.author_edit.text().strip())
        opts = AddonOptions(
            input=str(self.input), title=self.title_edit.text().strip(),
            out_dir=str(Path(self.win.out_dir()) / "addons"), blender_exe=self.win.blender_exe(),
            gmod_dir=str(self.win.gmod_dir), author=self.author_edit.text().strip() or "gm2b",
            surface=self.surface_box.currentData(), mass=self.mass_box.value(),
            scale=self.scale_box.currentData(), rotate_deg=self.rotate_box.currentData(),
            tags=(self.tag_box.currentData(),), install=self.install_box.isChecked(),
            make_gma=self.gma_box.isChecked(),
        )
        Path(opts.out_dir).mkdir(parents=True, exist_ok=True)
        self.build_btn.setEnabled(False)
        self.result_card.hide()

        def done(res):
            self.result = res
            lines = [f"Модель: {res.model_path}", f"Треугольников: {res.stats.get('triangles', '—')}, "
                     f"материалов: {len(res.stats.get('materials', []))}", f"Папка аддона: {res.folder}"]
            if res.gma:
                lines.append(f"Для Workshop: {res.gma}")
            if res.installed:
                lines.append(f"Установлено в игру: {res.installed}")
            self.result_text.setText("\n".join(lines))
            self.result_card.show()
            self.update_button()

        def job(w: Worker):
            return build_addon(opts, w.log.emit)

        self.win.run_bg(job, done)

    def check_in_blender(self):
        if not self.result:
            return
        res = self.result
        vfs = VirtualFS([FolderSource(res.folder), *self.win.game])
        opts = ExportOptions(out_dir=str(Path(self.win.out_dir()) / "addons" / "_check"),
                             blender_exe=self.win.blender_exe(), open_after=True)
        self.win.run_bg(lambda w: export_model(vfs, res.model_path, opts, w.log.emit), lambda _: None)


# ================================================================== Настройки

class SettingsPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__(objectName="Page")
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 18)
        lay.setSpacing(16)
        lay.addLayout(page_header("Настройки", "Программы, папки и плагин для Blender"))

        column = QVBoxLayout()
        column.setSpacing(14)

        c, cl = card(margins=18)
        cl.addLayout(section("Blender", "cube"))
        row = QHBoxLayout()
        self.blender_edit = QLineEdit(win.settings.value("blender", "") or (find_blender() or ""))
        self.blender_edit.setPlaceholderText("Путь к blender.exe")
        self.blender_edit.textChanged.connect(win.refresh_status)
        b = QPushButton(icon("folder"), " Обзор")
        b.clicked.connect(self._pick_blender)
        row.addWidget(self.blender_edit, 1)
        row.addWidget(b)
        cl.addLayout(row)
        cl.addWidget(label("Нужен для экспорта (Blender работает в фоне, окно не открывается) и для "
                           "сборки аддонов из FBX/OBJ/glTF.", "Hint", wrap=True))
        column.addWidget(c)

        c, cl = card(margins=18)
        self.plugin_badge = label("", "Badge")
        cl.addLayout(section("Плагин GMod Tools для Blender", "plug", self.plugin_badge))
        cl.addWidget(label("Вкладка «GMod» в N-панели Blender: слайдеры Face Poser, скины, bodygroups "
                           "и пресеты выражений — как в Garry's Mod.", "Muted", wrap=True))
        self.plugin_btn = QPushButton(icon("plug"), " Установить плагин")
        self.plugin_btn.clicked.connect(self.install_plugin)
        cl.addWidget(self.plugin_btn, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(c)

        c, cl = card(margins=18)
        cl.addLayout(section("Garry's Mod", "game"))
        self.gmod_label = label(str(win.gmod_dir) if win.gmod_dir else "Не найден — проверьте, что игра "
                                "установлена через Steam", "Muted", wrap=True)
        cl.addWidget(self.gmod_label)
        self.content_label = label("", "Hint", wrap=True)
        cl.addWidget(self.content_label)
        column.addWidget(c)

        c, cl = card(margins=18)
        cl.addLayout(section("Папка для результатов", "folder"))
        row = QHBoxLayout()
        default_out = str(Path.home() / "Documents" / "GMod2Blender")
        self.out_edit = QLineEdit(win.settings.value("out", "") or default_out)
        self.out_edit.editingFinished.connect(lambda: win.settings.setValue("out", self.out_edit.text().strip()))
        b1 = QPushButton(icon("folder"), " Обзор")
        b1.clicked.connect(self._pick_out)
        b2 = QPushButton("Открыть")
        b2.clicked.connect(lambda: open_folder(Path(self.out_edit.text())))
        row.addWidget(self.out_edit, 1)
        row.addWidget(b1)
        row.addWidget(b2)
        cl.addLayout(row)
        column.addWidget(c)

        column.addWidget(label(f"{APP_NAME} {__version__}", "Hint"))
        column.addStretch()
        wrap = QHBoxLayout()
        wrap.addLayout(column, 1)
        wrap.addStretch(0)
        holder = QWidget()
        holder.setMaximumWidth(860)
        holder.setLayout(wrap)
        lay.addWidget(holder, 1)

    def _pick_blender(self):
        f, _ = QFileDialog.getOpenFileName(self, "blender.exe", "", "Blender (blender.exe blender)")
        if f:
            self.blender_edit.setText(f)
            self.win.settings.setValue("blender", f)

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Папка вывода", self.out_edit.text())
        if d:
            self.out_edit.setText(d)
            self.win.settings.setValue("out", d)

    def install_plugin(self):
        exe = self.win.blender_exe()
        if not Path(exe).is_file():
            QMessageBox.warning(self, "Плагин", "Сначала укажите путь к blender.exe")
            return
        self.plugin_btn.setEnabled(False)
        self.win.log("Устанавливаю плагин GMod Tools в Blender…")

        def done(where):
            self.win.log(f"✔ Плагин установлен и включён: {where}")
            self.plugin_btn.setEnabled(True)
            self.win.refresh_status()
            self.win.notify("Плагин установлен", "В Blender: 3D-вид → клавиша N → вкладка «GMod».\n"
                                                 "Если Blender был открыт — перезапустите его.")

        self.win.run_bg(lambda w: install_addon(exe), done)


# ================================================================== окно

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Garry's Mod ⇄ Blender")
        self.resize(1400, 880)
        self.setMinimumSize(1100, 700)
        self.setAcceptDrops(True)
        self.settings = QSettings("gm2b", "gm2b")
        self.gmod_dir = find_gmod_dir()
        self.game = game_sources(self.gmod_dir)
        self.content: list[GmaSource] = []
        self._tasks: list[Task] = []

        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        main.addWidget(self._build_logbar())
        h.addLayout(main, 1)

        # Настройки создаём первыми: остальные страницы берут из них пути
        self.settings_page = SettingsPage(self)
        self.import_page = ImportPage(self)
        self.addon_page = AddonPage(self)
        from .character_page import CharacterPage

        self.character_page = CharacterPage(self)
        for page in (self.import_page, self.addon_page, self.character_page, self.settings_page):
            self.stack.addWidget(page)
        self.nav_group.buttons()[0].setChecked(True)

        self.refresh_status()
        self.log(f"{APP_NAME} {__version__}")
        if self.gmod_dir:
            self.log(f"Garry's Mod: {self.gmod_dir} (VPK: {len(self.game) - 1})")
        else:
            self.log("Garry's Mod не найден — текстуры из базовой игры искаться не будут")
        self._load_content()

    # ---------------------------------------------------------------- каркас

    def _build_sidebar(self):
        side = QFrame(objectName="Sidebar")
        side.setFixedWidth(232)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 20, 16, 16)
        lay.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        logo = QLabel(objectName="Logo")
        logo.setFixedSize(42, 42)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(pixmap("cube", "#ffffff", 24))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label(APP_NAME, "AppName"))
        names.addWidget(label("Garry's Mod ⇄ Blender", "AppSub"))
        brand.addLayout(names)
        lay.addLayout(brand)
        lay.addSpacing(22)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, (name, title) in enumerate((("import", "Из GMod"), ("addon", "В GMod"),
                                           ("person", "Свой персонаж"), ("settings", "Настройки"))):
            b = QPushButton(icon(name, "#8b91a1", "#ffffff", 19), f"   {title}", objectName="Nav", checkable=True)
            b.setIconSize(QSize(19, 19))
            b.clicked.connect(lambda _=False, i=i: self.stack.setCurrentIndex(i))
            self.nav_group.addButton(b, i)
            lay.addWidget(b)
        lay.addStretch()

        lay.addWidget(label("СОСТОЯНИЕ", "Hint"))
        self.status_labels = {}
        for key in ("gmod", "blender", "plugin"):
            lbl = label("", "StatusDot")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            self.status_labels[key] = lbl
            lay.addWidget(lbl)
        return side

    def _build_logbar(self):
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setFixedHeight(170)
        self.log_view.hide()
        v.addWidget(self.log_view)
        bar = QFrame(objectName="LogBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 8, 14, 8)
        h.setSpacing(10)
        h.addWidget(icon_label("terminal", MUTED, 16))
        self.log_line = label("", "LogLine")
        h.addWidget(self.log_line, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(200)
        self.progress.hide()
        h.addWidget(self.progress)
        self.log_btn = QPushButton("Журнал", objectName="Ghost", checkable=True)
        self.log_btn.toggled.connect(self.log_view.setVisible)
        h.addWidget(self.log_btn)
        v.addWidget(bar)
        return wrap

    # ---------------------------------------------------------------- общее состояние

    def blender_exe(self) -> str:
        return self.settings_page.blender_edit.text().strip()

    def blender_ok(self) -> bool:
        return Path(self.blender_exe() or "-").is_file()

    def out_dir(self) -> str:
        out = self.settings_page.out_edit.text().strip()
        self.settings.setValue("out", out)
        self.settings.setValue("blender", self.blender_exe())
        return out

    def refresh_status(self, *_):
        def dot(ok, text):
            color = OK if ok else BAD
            return f'<span style="color:{color}">●</span>&nbsp;&nbsp;{text}'

        plugin = addon_installed()
        self.status_labels["gmod"].setText(dot(bool(self.gmod_dir), "Garry's Mod"))
        self.status_labels["blender"].setText(dot(self.blender_ok(), "Blender"))
        self.status_labels["plugin"].setText(dot(plugin, "Плагин Blender"))
        if hasattr(self, "settings_page"):
            sp = self.settings_page
            sp.plugin_badge.setText("установлен" if plugin else "не установлен")
            sp.plugin_btn.setText(" Переустановить плагин" if plugin else " Установить плагин")
            sp.content_label.setText(f"Подключено аддонов Workshop для поиска текстур и моделей: "
                                     f"{len(self.content)}")
        if hasattr(self, "import_page"):
            self.import_page.update_export_button()
        if hasattr(self, "addon_page"):
            self.addon_page.update_button()
        if hasattr(self, "character_page"):
            self.character_page.update_buttons()

    def _load_content(self):
        """Фоном подключаем все скачанные аддоны Workshop как источник текстур/моделей."""
        paths = find_workshop_addons(self.gmod_dir)
        if not paths:
            return

        def job(w: Worker):
            out = []
            for i, p in enumerate(paths):
                w.progress.emit(i + 1, len(paths))
                try:
                    out.append(GmaSource(p))
                except Exception:  # noqa: BLE001 — битые аддоны пропускаем
                    pass
            return out

        def done(found):
            self.content = found
            self.log(f"Контент Workshop: {len(found)} аддонов подключено для поиска текстур и моделей")
            self.refresh_status()
            self.import_page.content_loaded()

        self.run_bg(job, done)

    def notify(self, title: str, text: str, folder: Path | None = None):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIconPixmap(pixmap("check", OK, 32))
        open_btn = box.addButton("Открыть папку", QMessageBox.ButtonRole.ActionRole) if folder else None
        box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if open_btn is not None and box.clickedButton() is open_btn:
            open_folder(folder)

    # ---------------------------------------------------------------- фоновые задачи

    def run_bg(self, fn, on_done):
        def done(result):
            self._tasks.remove(task)
            self.progress.setVisible(bool(self._tasks))
            on_done(result)
            self.refresh_status()

        def failed(error):
            self._tasks.remove(task)
            self.progress.setVisible(bool(self._tasks))
            self.log("✖ " + error.splitlines()[0])
            self.refresh_status()
            QMessageBox.critical(self, "Ошибка", error[:2000])

        task = Task(self, fn, done, failed)
        task.worker.log.connect(self.log)
        task.worker.progress.connect(self._on_progress)
        self._tasks.append(task)
        self.progress.setMaximum(0)  # «бегущая» полоса, пока не пришёл прогресс
        self.progress.show()
        if hasattr(self, "character_page"):
            self.character_page.update_buttons()  # пока идёт задача, шаги мастера недоступны
        task.thread.start()

    @Slot(str)
    def log(self, text: str):
        import logging
        logging.getLogger("gm2b").info(text)
        self.log_view.appendPlainText(text)
        self.log_line.setText(text.strip().splitlines()[-1] if text.strip() else "")

    @Slot(int, int)
    def _on_progress(self, cur, total):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(cur)

    # ---------------------------------------------------------------- drag & drop

    def closeEvent(self, event):
        if self._tasks:
            QMessageBox.information(self, "Задача ещё выполняется",
                                    "Дождитесь завершения обработки перед закрытием программы.")
            event.ignore()
            return
        event.accept()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            if self.stack.currentWidget() is self.addon_page:
                self.addon_page.drop.setProperty("active", True)
                self.addon_page.drop.style().polish(self.addon_page.drop)

    def dragLeaveEvent(self, e):
        self.addon_page.drop.setProperty("active", False)
        self.addon_page.drop.style().polish(self.addon_page.drop)

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        for p in paths:
            ext = Path(p).suffix.lower()
            if ext in INPUT_EXTENSIONS and self.stack.currentWidget() is self.character_page:
                self.character_page.set_input(p)  # модель брошена в мастер персонажа
            elif ext in INPUT_EXTENSIONS:  # 3D-модель — в раздел «В GMod»
                self.nav_group.button(1).click()
                self.addon_page.set_input(p)
            else:  # .gma/.bin или папка — в раздел «Из GMod»
                self.nav_group.button(0).click()
                self.import_page.add_addon(p)


def style_sheet() -> str:
    """QSS + картинки для галочек и стрелок (Qt берёт их только из файлов)."""
    import tempfile

    assets = Path(tempfile.gettempdir()) / "gm2b_ui"
    assets.mkdir(exist_ok=True)
    check = assets / "check.png"
    arrow = assets / "chevron.png"
    pixmap("check", "#ffffff", 14, ratio=2).save(str(check))
    pixmap("chevron", MUTED, 14, ratio=2).save(str(arrow))
    return QSS + (
        f"QCheckBox::indicator:checked {{ image: url({check.as_posix()}); }}\n"
        f"QComboBox::down-arrow {{ image: url({arrow.as_posix()}); width: 12px; height: 12px; }}\n"
    )


def main():
    from .diagnostics import configure
    configure()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI Variable Text", 10))
    app.setStyleSheet(style_sheet())
    win = MainWindow()
    win.show()
    for arg in sys.argv[1:]:
        win.import_page.add_addon(arg)
    sys.exit(app.exec())
