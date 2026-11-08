import sys


def smoke_test(report):
    """Exercise the packaged application without opening a window or loading user content."""
    import json
    import os
    from pathlib import Path
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from gm2b import app as module
    from gm2b import __version__
    module.find_gmod_dir = lambda: None
    module.find_workshop_addons = lambda *_: []
    application = QApplication([])
    application.setStyleSheet(module.style_sheet())
    window = module.MainWindow()
    for i in range(window.stack.count()):
        window.stack.setCurrentIndex(i)
        application.processEvents()
    root = Path(module.__file__).parent
    scripts = ["blender_build.py", "blender_build_map.py", "blender_char_prepare.py",
               "blender_char_rig.py", "blender_to_source.py", "blender_addon/gmod_face.py"]
    missing = [p for p in scripts if not (root / p).is_file()]
    result = {"version": __version__, "pages": window.stack.count(), "missing_scripts": missing}
    Path(report).write_text(json.dumps(result), encoding="utf-8")
    if missing or result["pages"] != 4:
        raise RuntimeError(str(result))

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        try:
            smoke_test(sys.argv[2])
        except BaseException:
            import traceback
            from pathlib import Path
            Path(sys.argv[2]).write_text(traceback.format_exc(), encoding="utf-8")
            sys.exit(1)
    else:
        from gm2b.app import main
        main()
