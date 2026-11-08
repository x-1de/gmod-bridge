"""Раздел «Свой персонаж»: модель без костей -> playermodel, как в Mixamo.

Шаги: 1 Модель → 2 Текстуры → 3 Точки (спереди и сбоку) → 4 Проверка → 5 Сборка.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGraphicsEllipseItem,
    QGraphicsItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .addon_builder import INPUT_EXTENSIONS
from .icons import icon
from .playermodel_builder import (
    IMAGE_EXT, PlayermodelOptions, build, default_depth, match_textures, prepare, rig,
)
from .theme import ACCENT, BAD, OK

# ключ, подпись, цвет, пара для симметрии
def _pair(key, title, color):
    return [(f"{key}_r", title, color, f"{key}_l"), (f"{key}_l", title, color, f"{key}_r")]


MARKERS = [
    ("chin", "Подбородок", "#ffd166", None),
    ("neck", "Шея", "#f7a072", None),
    *_pair("shoulder", "Плечо", "#c792ea"),
    *_pair("elbow", "Локоть", "#ff5c7a"),
    *_pair("wrist", "Запястье", "#ff8a3d"),
    *_pair("thumb", "Большой палец", "#ffe08a"),
    *_pair("fingertip", "Кончики пальцев", "#ffb3c1"),
    ("groin", "Пах", "#6ec6ff", None),
    *_pair("hip", "Бедро", "#8fa8ff"),
    *_pair("knee", "Колено", "#5fd38d"),
    *_pair("ankle", "Щиколотка", "#4dd0e1"),
    *_pair("toe", "Носок", "#9be7c4"),
]
HINTS = {"Подбородок": "низ подбородка", "Шея": "основание шеи, между ключицами",
         "Плечо": "центр плечевого сустава", "Локоть": "сгиб руки", "Запястье": "где кисть переходит в руку",
         "Большой палец": "кончик большого пальца — по нему поворачивается ладонь",
         "Кончики пальцев": "кончик среднего пальца", "Пах": "развилка ног",
         "Бедро": "тазобедренный сустав (верх ноги)", "Колено": "центр колена",
         "Щиколотка": "косточка над стопой", "Носок": "подушечка стопы (основание пальцев ног)"}
# на виде сбоку левая и правая точки совпадают — одна точка на пару
SIDE = {"chin": ["chin"], "neck": ["neck"], "shoulder": ["shoulder_r", "shoulder_l"],
        "elbow": ["elbow_r", "elbow_l"], "wrist": ["wrist_r", "wrist_l"], "thumb": ["thumb_r", "thumb_l"],
        "groin": ["groin"], "hip": ["hip_r", "hip_l"], "knee": ["knee_r", "knee_l"],
        "ankle": ["ankle_r", "ankle_l"], "toe": ["toe_r", "toe_l"]}
SIDE_OF = {k: s for s, keys in SIDE.items() for k in keys}
# у этих точек глубина задана по самой модели (вершина), а не серединой тела —
# при сдвиге спереди её не пересчитываем, пока пользователь не поправит сбоку
KEEP_DEPTH = {"thumb", "toe"}
STEPS = ["Модель", "Текстуры", "Точки", "Проверка", "Сборка"]


# ------------------------------------------------------------------ холст с точками

class Marker(QGraphicsEllipseItem):
    R = 7

    def __init__(self, key, title, color, canvas):
        super().__init__(-self.R, -self.R, 2 * self.R, 2 * self.R)
        self.key, self.canvas = key, canvas
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor("#0f1115"), 2.5))
        # точка не растёт при приближении — остаётся маленькой и точной
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges |
                      QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(10)
        self.label = QGraphicsSimpleTextItem(title, self)
        self.label.setBrush(QBrush(QColor("#e7e9ef")))
        self.label.setFont(QFont("Segoe UI Variable Text", 8, QFont.Weight.DemiBold))
        self.label.setPos(self.R + 3, -self.R)
        self.quiet = False

    def place(self, x, y):
        self.quiet = True
        self.setPos(QPointF(x, y))
        self.quiet = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self.quiet:
            self.canvas.moved(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        self.canvas.select(self)
        super().mousePressEvent(e)


class Canvas(QGraphicsView):
    """Картинка модели + перетаскиваемые точки (координаты — пиксели картинки)."""
    marker_moved = Signal(str)

    def __init__(self, center_lock: bool = True):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QBrush(QColor("#111318")))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.image = None
        self.markers: dict[str, Marker] = {}
        self.pairs: dict[str, str | None] = {}
        self.symmetric = center_lock
        self.center_lock = center_lock
        self.width_px = 0
        self.zoomed = False
        self.active: Marker | None = None
        self._pan = None
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # --- зум и перемещение: колесо — приблизить к курсору, правая/средняя кнопка — двигать
    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        factor = 1.2 ** steps
        current = self.transform().m11()
        fit = self._fit_scale()
        if current * factor < fit:  # не мельче, чем «вписать»
            self.fit()
            return
        self.scale(factor, factor)
        self.zoomed = True

    def _fit_scale(self):
        r = self.scene().sceneRect()
        if r.isEmpty():
            return 1.0
        return min(self.viewport().width() / r.width(), self.viewport().height() / r.height())

    def zoom_by(self, factor):
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.scale(factor, factor)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.zoomed = True
        if self.transform().m11() < self._fit_scale():
            self.fit()

    def mousePressEvent(self, e):
        if e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            delta = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if not isinstance(self.itemAt(e.position().toPoint()), Marker):
            self.fit()
        else:
            super().mouseDoubleClickEvent(e)

    # --- точная подстройка: стрелки двигают выбранную точку на 1 пиксель (Shift — на 5)
    def select(self, marker: Marker):
        if self.active is not None and self.active is not marker:
            self.active.setPen(QPen(QColor("#0f1115"), 2.5))
        self.active = marker
        marker.setPen(QPen(QColor("#ffffff"), 2.5))
        self.setFocus()

    def keyPressEvent(self, e):
        moves = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if self.active is not None and e.key() in moves:
            # шаг — один пиксель экрана при текущем приближении (Shift — пять)
            step = self.keyPressEvent_scale() * (5 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
            dx, dy = moves[e.key()]
            self.active.setPos(self.active.pos() + QPointF(dx * step, dy * step))
            return
        super().keyPressEvent(e)

    def show_image(self, path: str, with_markers: bool):
        sc = self.scene()
        for item in list(sc.items()):
            if not isinstance(item, Marker) and item.parentItem() is None:
                sc.removeItem(item)
        pm = QPixmap(path)
        self.image = sc.addPixmap(pm)
        self.width_px = pm.width()
        sc.setSceneRect(QRectF(pm.rect()).adjusted(-20, -20, 20, 20))
        for m in self.markers.values():
            m.setVisible(with_markers)
        self.fit()

    def add_marker(self, key, title, color, pair=None):
        if key not in self.markers:
            self.markers[key] = Marker(key, title, color, self)
            self.scene().addItem(self.markers[key])
        self.pairs[key] = pair
        return self.markers[key]

    def keyPressEvent_scale(self):
        """Шаг стрелок в пикселях картинки: 1 пиксель экрана при текущем приближении."""
        return max(1.0 / max(self.transform().m11(), 1e-3), 0.25)

    def moved(self, marker: Marker):
        pair = self.pairs.get(marker.key)
        if self.symmetric and pair and pair in self.markers:
            self.markers[pair].place(self.width_px - marker.pos().x(), marker.pos().y())
        elif self.center_lock and self.symmetric and pair is None:  # подбородок и пах — по центру
            marker.place(self.width_px / 2, marker.pos().y())
        self.marker_moved.emit(marker.key)

    def draw_skeleton(self, joints_px: dict, parents: dict):
        sc = self.scene()
        pen = QPen(QColor(ACCENT), 2.5)
        for child, parent in parents.items():
            if child in joints_px and parent in joints_px:
                a, b = joints_px[child], joints_px[parent]
                sc.addLine(a[0], a[1], b[0], b[1], pen).setZValue(5)
        for x, y in joints_px.values():
            sc.addEllipse(x - 3.5, y - 3.5, 7, 7, QPen(QColor("#0f1115"), 1.5),
                          QBrush(QColor("#ffffff"))).setZValue(6)

    def fit(self):
        if self.image:
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self.zoomed = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.zoomed:
            self.fit()


# ------------------------------------------------------------------ раздел

class CharacterPage(QWidget):
    def __init__(self, win):
        super().__init__(objectName="Page")
        self.win = win
        self.input: Path | None = None
        self.session = None
        self.result = None
        self.textures: dict[str, dict] = {}   # ручные текстуры: {материал: {"base": путь, "normal": путь}}
        self.manual_depth: set[str] = set()   # точки вида сбоку, которые пользователь двигал сам

        from .app import card, label, page_header, section  # общие мелкие виджеты окна

        self._card, self._label, self._section = card, label, section
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 18)
        lay.setSpacing(14)
        head = QHBoxLayout()
        head.addLayout(page_header("Свой персонаж",
                                   "Модель без костей → playermodel с анимациями игрока, как в Mixamo"))
        head.addStretch()
        head.addLayout(self._build_stepper())
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(16)
        canvas_card, cl = card(margins=8)
        views = QHBoxLayout()
        views.setSpacing(8)
        self.canvas = Canvas(center_lock=True)
        self.side = Canvas(center_lock=False)
        self.side.symmetric = False
        self.side.hide()
        views.addWidget(self.canvas, 3)
        views.addWidget(self.side, 2)
        cl.addLayout(views, 1)
        self.canvas.marker_moved.connect(self.front_moved)
        self.side.marker_moved.connect(self.side_moved)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 0, 6, 0)
        self.canvas_hint = label("Загрузите модель — здесь появится вид спереди", "Hint")
        bar.addWidget(self.canvas_hint, 1)
        for text, tip, action in (("－", "Отдалить", lambda: self._zoom(1 / 1.4)),
                                  ("＋", "Приблизить", lambda: self._zoom(1.4)),
                                  ("Вписать", "Показать модель целиком", self._fit_all)):
            b = QPushButton(text, objectName="Ghost")
            b.setToolTip(tip)
            b.clicked.connect(action)
            bar.addWidget(b)
        cl.addLayout(bar)
        row.addWidget(canvas_card, 7)
        self.panels = QStackedWidget(objectName="Panels")
        for build_panel in (self._panel_model, self._panel_textures, self._panel_points,
                            self._panel_check, self._panel_build):
            self.panels.addWidget(build_panel())
        side_card, sl = card(margins=20)
        sl.addWidget(self.panels)
        side_card.setMinimumWidth(390)
        row.addWidget(side_card, 4)
        lay.addLayout(row, 1)
        self.go(0)

    # ---------------------------------------------------------------- каркас

    def _build_stepper(self):
        box = QHBoxLayout()
        box.setSpacing(6)
        self.step_buttons = []
        for i, title in enumerate(STEPS):
            b = QPushButton(f"{i + 1}  {title}", objectName="Chip", checkable=True)
            b.setEnabled(False)
            b.clicked.connect(lambda _=False, i=i: self.go(i))
            self.step_buttons.append(b)
            box.addWidget(b)
        return box

    def go(self, step: int):
        self.panels.setCurrentIndex(step)
        for i, b in enumerate(self.step_buttons):
            b.setChecked(i == step)
        self.side.setVisible(step == 2 and self.session is not None)
        if self.session and step in (1, 2):
            self.canvas.show_image(str(self.session.work / "front.png"), with_markers=step == 2)
        if step == 2 and self.session:
            self.side.show_image(str(self.session.work / "side.png"), with_markers=True)
        if step == 3 and self.session and self.session.rig:
            self.show_check("skeleton")

    def _zoom(self, factor):
        for c in (self.canvas, self.side):
            if c.isVisible():
                c.zoom_by(factor)

    def _fit_all(self):
        self.canvas.fit()
        self.side.fit()

    def unlock(self, upto: int):
        for i, b in enumerate(self.step_buttons):
            b.setEnabled(i <= upto)

    def _panel(self, title, icon_name, text=None):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addLayout(self._section(title, icon_name))
        if text:
            lay.addWidget(self._label(text, "Muted", wrap=True))
        return w, lay

    # ---------------------------------------------------------------- 1. модель

    def _panel_model(self):
        w, lay = self._panel("1. Модель", "person", "Персонаж в T-позе или A-позе (руки разведены в стороны). "
                                                     "Кости не нужны — их создаст программа.")
        self.file_label = self._label("Файл не выбран", "Section", wrap=True)
        lay.addWidget(self.file_label)
        pick = QPushButton(icon("folder"), " Выбрать модель")
        pick.clicked.connect(self.pick_file)
        lay.addWidget(pick)
        lay.addWidget(self._label("FBX · OBJ · glTF / GLB · BLEND · STL · PLY — или перетащите файл в окно",
                                  "Hint", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.height_box = QDoubleSpinBox(decimals=0, minimum=60, maximum=260, value=183, suffix=" см")
        self.height_box.setToolTip("Рост в игре. Стандартный игрок GMod — 183 см (72 юнита)")
        self.rotate_box = QComboBox()
        for deg in (0, 90, 180, 270):
            self.rotate_box.addItem(f"{deg}°", deg)
        self.rotate_box.setToolTip("Если на виде спереди персонаж стоит боком или спиной — поверните")
        grid.addWidget(self._label("Рост в игре", "Muted"), 0, 0)
        grid.addWidget(self.height_box, 0, 1)
        grid.addWidget(self._label("Поворот", "Muted"), 1, 0)
        grid.addWidget(self.rotate_box, 1, 1)
        lay.addLayout(grid)
        lay.addStretch()
        self.prepare_btn = QPushButton(icon("play", "#ffffff"), "  Подготовить", objectName="Primary")
        self.prepare_btn.clicked.connect(self.run_prepare)
        lay.addWidget(self.prepare_btn)
        return w

    def pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Модель персонажа", "",
                                           "3D-модели (" + " ".join(f"*{e}" for e in INPUT_EXTENSIONS) + ")")
        if f:
            self.set_input(f)

    def set_input(self, path: str):
        p = Path(path)
        if p.suffix.lower() not in INPUT_EXTENSIONS:
            self.win.log(f"✖ {p.name}: формат не поддерживается")
            return
        self.input = p
        self.file_label.setText(p.name)
        if not self.name_edit.text().strip():
            self.name_edit.setText(p.stem.replace("_", " ").title())
        self.go(0)
        self.update_buttons()

    def update_buttons(self, *_):
        tools = self.win.blender_ok() and bool(self.win.gmod_dir)
        busy = bool(self.win._tasks)
        if hasattr(self, "prepare_btn"):
            self.prepare_btn.setEnabled(self.input is not None and tools and not busy)
        if hasattr(self, "rig_btn"):
            self.rig_btn.setEnabled(self.session is not None and tools and not busy)
        if hasattr(self, "build_btn"):
            self.build_btn.setEnabled(bool(self.session and self.session.rig) and tools and not busy
                                      and bool(self.name_edit.text().strip()))

    def run_prepare(self):
        height_in = self.height_box.value() / 2.54
        rotate = self.rotate_box.currentData()
        path, exe = str(self.input), self.win.blender_exe()

        def done(session):
            self.session = session
            self.textures = {}
            self.manual_depth = set()
            self.place_markers(session.prepare["markers"])
            self.fill_materials()
            self.canvas_hint.setText(f"{session.prepare['triangles']:,} треугольников · "
                                     f"{len(session.prepare['materials'])} материалов".replace(",", " "))
            self.unlock(2)
            self.go(1)

        self.win.run_bg(lambda w: prepare(path, exe, height_in, rotate, w.log.emit), done)

    # ---------------------------------------------------------------- 2. текстуры

    def _panel_textures(self):
        w, lay = self._panel("2. Текстуры", "image", "Проверьте, у всех ли материалов есть картинка. "
                                                     "Если текстуры не подтянулись при импорте — назначьте их "
                                                     "вручную или укажите папку: программа подберёт по именам.")
        self.mat_list = QListWidget()
        self.mat_list.setIconSize(QSize(44, 44))
        self.mat_list.currentRowChanged.connect(lambda *_: self.update_tex_buttons())
        lay.addWidget(self.mat_list, 1)
        self.tex_summary = self._label("", "Hint", wrap=True)
        lay.addWidget(self.tex_summary)
        row = QHBoxLayout()
        self.tex_base_btn = QPushButton(icon("image"), " Текстура…")
        self.tex_base_btn.clicked.connect(lambda: self.assign_texture("base"))
        self.tex_norm_btn = QPushButton("Нормали…")
        self.tex_norm_btn.setToolTip("Карта нормалей (синеватая картинка) — объём и рельеф поверхности")
        self.tex_norm_btn.clicked.connect(lambda: self.assign_texture("normal"))
        self.tex_clear_btn = QPushButton("Сбросить")
        self.tex_clear_btn.clicked.connect(self.clear_texture)
        row.addWidget(self.tex_base_btn)
        row.addWidget(self.tex_norm_btn)
        row.addWidget(self.tex_clear_btn)
        lay.addLayout(row)
        folder = QPushButton(icon("folder"), " Подобрать из папки…")
        folder.clicked.connect(self.textures_from_folder)
        lay.addWidget(folder)
        nxt = QPushButton(icon("play", "#ffffff"), "  Дальше: точки", objectName="Primary")
        nxt.clicked.connect(lambda: self.go(2))
        lay.addWidget(nxt)
        return w

    def material_texture(self, m) -> tuple[str | None, str | None, bool]:
        """(картинка, нормали, назначено вручную) для материала."""
        o = self.textures.get(m["name"], {})
        return o.get("base") or m["base"], o.get("normal") or m["normal"], bool(o)

    def fill_materials(self):
        row = max(self.mat_list.currentRow(), 0)
        self.mat_list.clear()
        missing = 0
        for m in self.session.prepare["materials"]:
            base, normal, manual = self.material_texture(m)
            it = QListWidgetItem()
            if base and Path(base).exists():
                it.setIcon(QIcon(QPixmap(base).scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation)))
                status = "назначена вручную" if manual else "из модели"
                it.setText(f"{m['name']}\n{status}" + (" · есть нормали" if normal else ""))
            else:
                it.setIcon(icon("x", BAD, size=22))
                it.setText(f"{m['name']}\nнет текстуры — будет залит цветом")
                it.setForeground(QBrush(QColor("#ffb0b0")))
                missing += 1
            self.mat_list.addItem(it)
        total = len(self.session.prepare["materials"])
        self.tex_summary.setText(f"С текстурой: {total - missing} из {total}" +
                                 ("" if not missing else " — назначьте недостающие"))
        self.tex_summary.setStyleSheet(f"color: {OK if not missing else BAD};")
        self.mat_list.setCurrentRow(min(row, total - 1))
        self.update_tex_buttons()

    def update_tex_buttons(self):
        ok = self.session is not None and self.mat_list.currentRow() >= 0
        for b in (self.tex_base_btn, self.tex_norm_btn, self.tex_clear_btn):
            b.setEnabled(ok)

    def assign_texture(self, kind: str):
        row = self.mat_list.currentRow()
        if row < 0:
            return
        name = self.session.prepare["materials"][row]["name"]
        f, _ = QFileDialog.getOpenFileName(self, f"Текстура для «{name}»", "",
                                           "Картинки (" + " ".join(f"*{e}" for e in IMAGE_EXT) + ")")
        if f:
            self.textures.setdefault(name, {})[kind] = f
            self.fill_materials()

    def clear_texture(self):
        row = self.mat_list.currentRow()
        if row >= 0:
            self.textures.pop(self.session.prepare["materials"][row]["name"], None)
            self.fill_materials()

    def textures_from_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с текстурами")
        if not d:
            return
        names = [m["name"] for m in self.session.prepare["materials"]]
        found = match_textures(d, names)
        for name, tex in found.items():
            self.textures.setdefault(name, {}).update(tex)
        self.win.log(f"Текстуры из папки: подобрано для {len(found)} из {len(names)} материалов")
        self.fill_materials()

    # ---------------------------------------------------------------- 3. точки

    def _panel_points(self):
        w, lay = self._panel("3. Расставьте точки", "bone",
                             "Слева — вид спереди (правая рука персонажа слева), справа — сбоку: там точки "
                             "двигаются вперёд-назад, чтобы кость шла по центру руки или ноги. Высота общая.")
        lay.addWidget(self._label("Колесо мыши — приблизить · правая кнопка — двигать вид · двойной клик — "
                                  "вписать · стрелки — сдвиг выбранной точки на 1 пиксель (Shift — на 5)",
                                  "Hint", wrap=True))
        legend = QGridLayout()
        legend.setVerticalSpacing(3)
        legend.setHorizontalSpacing(6)
        seen = []
        for _key, title, color, _pair in MARKERS:
            if title in seen:
                continue
            seen.append(title)
            i = len(seen) - 1
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 12pt;")
            text = self._label(title, "Muted")
            text.setToolTip(HINTS[title])
            dot.setToolTip(HINTS[title])
            legend.addWidget(dot, i // 2, (i % 2) * 2)
            legend.addWidget(text, i // 2, (i % 2) * 2 + 1)
        legend.setColumnStretch(1, 1)
        legend.setColumnStretch(3, 1)
        lay.addLayout(legend)
        self.sym_box = QCheckBox("Симметрия (левая и правая стороны вместе)")
        self.sym_box.setChecked(True)
        self.sym_box.toggled.connect(lambda v: setattr(self.canvas, "symmetric", v))
        lay.addWidget(self.sym_box)
        self.fingers_box = QCheckBox("Пальцы — по 15 костей на руку (хват оружия)")
        self.fingers_box.setChecked(True)
        self.fingers_box.setToolTip("Анимации игрока сгибают пальцы вокруг оружия. Выключите, если у модели "
                                    "кисти-варежки или пальцы слиплись в один кусок.")
        lay.addWidget(self.fingers_box)
        self.gender_box = QComboBox()
        self.gender_box.addItem("Мужские анимации", "male")
        self.gender_box.addItem("Женские анимации", "female")
        self.gender_box.setToolTip("Какой набор анимаций игрока использовать: походка, позы, жесты")
        lay.addWidget(self.gender_box)
        reset = QPushButton("Вернуть автоматические точки")
        reset.clicked.connect(lambda: (self.manual_depth.clear(),
                                       self.place_markers(self.session.prepare["markers"])))
        lay.addWidget(reset)
        lay.addStretch()
        self.rig_btn = QPushButton(icon("bone", "#ffffff"), "  Построить скелет", objectName="Primary")
        self.rig_btn.clicked.connect(self.run_rig)
        lay.addWidget(self.rig_btn)
        return w

    # координаты: вид спереди — (x, z), вид сбоку — (y, z); правая сторона кадра сбоку = −Y
    def to_px(self, xz):
        m = self.session.prepare["front"]
        return (m["w"] / 2 + (xz[0] - m["cx"]) / m["upp"], m["h"] / 2 - (xz[1] - m["cz"]) / m["upp"])

    def to_world(self, p):
        m = self.session.prepare["front"]
        return [m["cx"] + (p[0] - m["w"] / 2) * m["upp"], m["cz"] - (p[1] - m["h"] / 2) * m["upp"]]

    def side_px(self, y, z):
        m = self.session.prepare["side"]
        return (m["w"] / 2 - (y - m["cx"]) / m["upp"], m["h"] / 2 - (z - m["cz"]) / m["upp"])

    def side_world(self, p):
        m = self.session.prepare["side"]
        return m["cx"] - (p[0] - m["w"] / 2) * m["upp"], m["cz"] - (p[1] - m["h"] / 2) * m["upp"]

    def place_markers(self, world: dict):
        for key, title, color, pair in MARKERS:
            self.canvas.add_marker(key, title, color, pair).place(*self.to_px(world[key][:2]))
        for skey, keys in SIDE.items():
            title, color = next((t, c) for k, t, c, _p in MARKERS if k == keys[0])
            v = world[keys[0]]
            y = v[2] if len(v) > 2 else default_depth(self.session, keys[0], v[0], v[1])
            self.side.add_marker(skey, title, color).place(*self.side_px(y, v[1]))

    def front_moved(self, key):
        """Точку сдвинули спереди: на виде сбоку та же высота, глубина — по модели (если не правили)."""
        skey = SIDE_OF.get(key)
        side = self.side.markers.get(skey) if skey else None
        if side is None:
            return
        x, z = self.to_world((self.canvas.markers[key].pos().x(), self.canvas.markers[key].pos().y()))
        if skey in self.manual_depth or skey in KEEP_DEPTH:
            y, _ = self.side_world((side.pos().x(), side.pos().y()))
        else:
            y = default_depth(self.session, key, x, z)
        side.place(*self.side_px(y, z))

    def side_moved(self, skey):
        """Точку сдвинули сбоку: запоминаем глубину, высоту переносим на вид спереди."""
        self.manual_depth.add(skey)
        side = self.side.markers[skey]
        _y, z = self.side_world((side.pos().x(), side.pos().y()))
        for key in SIDE[skey]:
            m = self.canvas.markers[key]
            m.place(m.pos().x(), self.to_px((0, z))[1])

    def collect_markers(self) -> dict:
        out = {}
        for key, m in self.canvas.markers.items():
            x, z = self.to_world((m.pos().x(), m.pos().y()))
            if key in SIDE_OF:
                s = self.side.markers[SIDE_OF[key]]
                y, _ = self.side_world((s.pos().x(), s.pos().y()))
            else:  # кончики пальцев — глубина посередине пальцев
                y = default_depth(self.session, key, x, z)
            out[key] = [x, z, y]
        return out

    def run_rig(self):
        markers = self.collect_markers()
        session, exe, gmod = self.session, self.win.blender_exe(), self.win.gmod_dir
        gender, fingers, textures = self.gender_box.currentData(), self.fingers_box.isChecked(), dict(self.textures)

        def done(s):
            total = s.prepare["vertices"]
            auto = total - s.rig["fixed_vertices"]
            self.weights_label.setText(f"Скелет: {s.rig['bone_count']} костей. Автоматические веса: "
                                       f"{100 * auto / max(total, 1):.0f}% вершин, остальные (пряди волос, "
                                       f"мелкие детали) — к ближайшим костям.")
            self.unlock(4)
            self.update_buttons()
            self.go(3)

        self.win.run_bg(lambda w: rig(session, markers, exe, gmod, gender, w.log.emit,
                                      fingers=fingers, textures=textures), done)

    # ---------------------------------------------------------------- 4. проверка

    def _panel_check(self):
        w, lay = self._panel("4. Проверка", "check",
                             "«В игре» — поза, в которой персонаж уходит в GMod: руки и ноги переведены в позу "
                             "эталонного скелета, чтобы анимации легли ровно. «Тест» — руки подняты, колено "
                             "согнуто, голова повёрнута. Если что-то рвётся или тянется не туда — поправьте точки.")
        views = QHBoxLayout()
        self.view_group = QButtonGroup(self)
        for key, title in (("skeleton", "Скелет"), ("ref", "В игре"), ("front", "Тест"), ("side", "Тест сбоку")):
            b = QPushButton(title, objectName="Chip", checkable=True)
            b.clicked.connect(lambda _=False, k=key: self.show_check(k))
            b.setProperty("view", key)
            self.view_group.addButton(b)
            views.addWidget(b)
        lay.addLayout(views)
        self.weights_label = self._label("", "Hint", wrap=True)
        lay.addWidget(self.weights_label)
        lay.addStretch()
        back = QPushButton(icon("back"), " Поправить точки")
        back.clicked.connect(lambda: self.go(2))
        lay.addWidget(back)
        nxt = QPushButton(icon("play", "#ffffff"), "  Всё хорошо, дальше", objectName="Primary")
        nxt.clicked.connect(lambda: self.go(4))
        lay.addWidget(nxt)
        return w

    def show_check(self, view: str):
        for b in self.view_group.buttons():
            b.setChecked(b.property("view") == view)
        work, info = self.session.work, self.session.rig
        if view == "skeleton":
            self.canvas.show_image(str(work / "front.png"), with_markers=False)
            joints = {k: self.to_px(v) for k, v in info["joints_front"].items()}
            self.canvas.draw_skeleton(joints, info["parents"])
        else:
            image = {"ref": "ref_front.png", "front": "test_front.png", "side": "test_side.png"}[view]
            self.canvas.show_image(str(work / image), with_markers=False)

    # ---------------------------------------------------------------- 5. сборка

    def _panel_build(self):
        w, lay = self._panel("5. Сборка · экспериментально", "addon")
        lay.addWidget(self._label("Автоматический риг требует проверки в игре. Если суставы сгибаются неверно, "
                                  "поправьте точки и веса в Blender.", "Hint", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.name_edit = QLineEdit(placeholderText="Имя в меню выбора модели")
        self.name_edit.textChanged.connect(self.update_buttons)
        self.author_edit = QLineEdit(self.win.settings.value("addon_author", "") or "gm2b")
        grid.addWidget(self._label("Название", "Muted"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(self._label("Папка автора", "Muted"), 1, 0)
        grid.addWidget(self.author_edit, 1, 1)
        lay.addLayout(grid)
        self.pm_install = QCheckBox("Сразу установить в Garry's Mod")
        self.pm_install.setChecked(bool(self.win.gmod_dir))
        self.pm_gma = QCheckBox("Собрать .gma для Workshop")
        self.pm_gma.setChecked(True)
        lay.addWidget(self.pm_install)
        lay.addWidget(self.pm_gma)
        lay.addWidget(self._label("В игре: меню C → Player Model → ваш персонаж. Анимации, рэгдолл после "
                                  "смерти и руки от первого лица подключаются автоматически.", "Hint", wrap=True))
        self.pm_result = self._label("", "Muted", wrap=True)
        self.pm_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.pm_result)
        self.pm_open = QPushButton(icon("folder"), " Открыть папку")
        self.pm_open.clicked.connect(lambda: os.startfile(str(self.result["folder"])))
        self.pm_open.hide()
        lay.addWidget(self.pm_open, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch()
        self.build_btn = QPushButton(icon("addon", "#ffffff"), "  Собрать персонажа", objectName="Primary")
        self.build_btn.clicked.connect(self.run_build)
        lay.addWidget(self.build_btn)
        self.update_buttons()
        return w

    def run_build(self):
        self.win.settings.setValue("addon_author", self.author_edit.text().strip())
        opts = PlayermodelOptions(
            title=self.name_edit.text().strip(), out_dir=str(Path(self.win.out_dir()) / "playermodels"),
            gmod_dir=str(self.win.gmod_dir), gender=self.gender_box.currentData(),
            author=self.author_edit.text().strip() or "gm2b", install=self.pm_install.isChecked(),
            make_gma=self.pm_gma.isChecked())
        Path(opts.out_dir).mkdir(parents=True, exist_ok=True)
        session = self.session

        def done(res):
            self.result = res
            lines = [f"Готово: {res['model']}"]
            if res["gma"]:
                lines.append(f"Для Workshop: {Path(res['gma']).name}")
            if res["installed"]:
                lines.append(f"Установлено в игру: addons/{Path(res['installed']).name}")
            self.pm_result.setText("\n".join(lines))
            self.pm_result.setStyleSheet(f"color: {OK};")
            self.pm_open.show()
            self.update_buttons()
            self.win.notify("Персонаж готов", "В игре: C → Player Model.\n" + "\n".join(lines), res["folder"])

        self.win.run_bg(lambda w: build(session, opts, w.log.emit), done)
