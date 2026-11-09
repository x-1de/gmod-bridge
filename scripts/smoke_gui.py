"""Start all pages without relying on a Steam installation or modifying user settings."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel
from gm2b.app import MainWindow, style_sheet

app = QApplication([])
app.setStyleSheet(style_sheet())
with patch("gm2b.app.find_gmod_dir", return_value=None), patch("gm2b.app.find_workshop_addons", return_value=[]):
    window = MainWindow()
    window.show()
    for i in range(window.stack.count()):
        window.stack.setCurrentIndex(i)
        app.processEvents()
    assert window.stack.count() == 4
    from gm2b.character_page import CharacterPage, MARKERS, SIDE_OF
    character = window.findChild(CharacterPage)
    assert character is not None
    assert "БЕТА" in character.findChild(QLabel, "CharacterBetaNotice").text()
    assert len(MARKERS) == 27
    assert character.palms_box.isChecked()
    character.fingers_box.setChecked(False)
    assert not character.palms_box.isEnabled()
    character.fingers_box.setChecked(True)
    for s in ("r", "l"):
        assert f"index_tip_{s}" in SIDE_OF
    QTimer.singleShot(50, app.quit)
    app.exec()
print("GUI smoke: all four pages opened")
