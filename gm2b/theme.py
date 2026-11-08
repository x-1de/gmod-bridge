"""Тема оформления: глубокий тёмный фон, карточки, тёпло-розовый градиент акцента."""

ACCENT = "#ff8a3d"
ACCENT2 = "#ff5c7a"
GRADIENT = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2})"
BG = "#0f1115"
SIDEBAR = "#13161c"
CARD = "#181b22"
FIELD = "#111318"
BORDER = "#252a34"
TEXT = "#e7e9ef"
MUTED = "#8b91a1"
OK = "#5fd38d"
BAD = "#ff6b6b"

QSS = f"""
* {{ outline: none; }}
QMainWindow, QDialog, QStackedWidget, QWidget#Page {{ background: {BG}; }}
QStackedWidget#Panels, QStackedWidget#Panels > QWidget {{ background: transparent; }}
QWidget {{ color: {TEXT}; font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif; font-size: 10pt; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: #1f232c; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

/* --- боковая панель */
QFrame#Sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QLabel#Logo {{ background: {GRADIENT}; border-radius: 12px; }}
QLabel#AppName {{ font-size: 12.5pt; font-weight: 700; color: #ffffff; }}
QLabel#AppSub {{ color: {MUTED}; font-size: 8.5pt; }}
QPushButton#Nav {{ text-align: left; padding: 10px 14px; border: none; border-radius: 10px;
                   background: transparent; color: #aeb4c2; font-size: 10.5pt; }}
QPushButton#Nav:hover {{ background: #1b1f27; color: #ffffff; }}
QPushButton#Nav:checked {{ background: rgba(255, 138, 61, 0.12); color: #ffffff; font-weight: 600; }}
QLabel#StatusDot {{ font-size: 9pt; color: {MUTED}; }}

/* --- заголовки страниц */
QLabel#PageTitle {{ font-size: 19pt; font-weight: 700; color: #ffffff; }}
QLabel#PageSub {{ color: {MUTED}; font-size: 10pt; }}
QLabel#Section {{ font-size: 11pt; font-weight: 600; color: #ffffff; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Hint {{ color: #6f7585; font-size: 9pt; }}
QLabel#ModelName {{ font-size: 15pt; font-weight: 700; color: #ffffff; }}
QLabel#StatValue {{ font-size: 15pt; font-weight: 700; color: {ACCENT}; }}
QLabel#StatTitle {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#Badge {{ background: rgba(255, 138, 61, 0.14); color: #ffb383; border-radius: 9px;
                padding: 2px 9px; font-size: 8.5pt; font-weight: 600; }}

/* --- карточки */
QFrame#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Stat {{ background: {FIELD}; border: 1px solid #1f232b; border-radius: 10px; }}
QFrame#Drop {{ background: {FIELD}; border: 2px dashed #2f3542; border-radius: 16px; }}
QFrame#Drop[active="true"] {{ border-color: {ACCENT}; background: rgba(255, 138, 61, 0.06); }}
QFrame#Result {{ background: rgba(95, 211, 141, 0.07); border: 1px solid rgba(95, 211, 141, 0.35);
                 border-radius: 14px; }}
QLabel#Preview {{ background: {FIELD}; border: 1px solid #1f232b; border-radius: 12px; color: #4d5361; }}

/* --- кнопки */
QPushButton {{ background: #20242d; border: 1px solid #2c313c; border-radius: 9px; padding: 8px 14px;
               color: {TEXT}; }}
QPushButton:hover {{ background: #272c37; border-color: #3a4050; }}
QPushButton:pressed {{ background: #1c2028; }}
QPushButton:disabled {{ color: #5d6371; background: #1a1d24; border-color: #22262f; }}
QPushButton#Primary {{ background: {GRADIENT}; border: none; color: #ffffff; font-weight: 700;
                       font-size: 11pt; padding: 12px 26px; border-radius: 11px; }}
QPushButton#Primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff9a55, stop:1 #ff7090); }}
QPushButton#Primary:disabled {{ background: #2a2420; color: #7a6a60; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton#Ghost:hover {{ color: #ffffff; }}
QPushButton#Chip {{ background: {FIELD}; border: 1px solid #2c313c; border-radius: 16px; padding: 7px 14px;
                    color: #aeb4c2; font-weight: 600; }}
QPushButton#Chip:hover {{ border-color: #46506a; color: #ffffff; }}
QPushButton#Chip:checked {{ background: rgba(255, 138, 61, 0.14); border-color: {ACCENT}; color: #ffffff; }}

/* --- поля */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{ background: {FIELD}; border: 1px solid #2a2f3a;
    border-radius: 9px; padding: 7px 10px; selection-background-color: {ACCENT}; min-height: 18px; }}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1b1f27; border: 1px solid {BORDER}; padding: 4px;
                               selection-background-color: rgba(255, 138, 61, 0.18); }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

/* --- списки */
QListWidget, QPlainTextEdit {{ background: {FIELD}; border: 1px solid #1f232b; border-radius: 11px;
                               padding: 5px; }}
QListWidget::item {{ padding: 8px 9px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: #191c23; }}
QListWidget::item:selected {{ background: rgba(255, 138, 61, 0.14); color: #ffd2b5; }}
QPlainTextEdit {{ font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 9pt; color: #a7adba; }}

QCheckBox {{ spacing: 9px; color: #c9ced8; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3a4050;
                        background: {FIELD}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QProgressBar {{ background: #1a1d24; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {GRADIENT}; border-radius: 3px; }}

QSplitter::handle {{ background: transparent; width: 12px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: #2d323d; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #3b4250; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 3px; }}
QScrollBar::handle:horizontal {{ background: #2d323d; border-radius: 4px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* --- журнал внизу */
QFrame#LogBar {{ background: {SIDEBAR}; border-top: 1px solid {BORDER}; }}
QLabel#LogLine {{ color: {MUTED}; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 9pt; }}
"""
