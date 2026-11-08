"""Start all pages without relying on a Steam installation or modifying user settings."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
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
    QTimer.singleShot(50, app.quit)
    app.exec()
print("GUI smoke: all four pages opened")
