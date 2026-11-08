"""Local rotating logs for bug reports; no telemetry."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def configure():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GModBridge" / "logs"
    try:
        root.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(root / "bridge.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger = logging.getLogger("gm2b")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
    except OSError:
        return
    previous = sys.excepthook

    def report(exc_type, value, tb):
        logger.critical("Unhandled exception", exc_info=(exc_type, value, tb))
        previous(exc_type, value, tb)

    sys.excepthook = report
