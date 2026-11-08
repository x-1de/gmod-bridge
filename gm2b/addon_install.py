"""Установка плагина GMod Tools в Blender пользователя."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ADDON_FILE = Path(__file__).with_name("blender_addon") / "gmod_face.py"
MODULE = ADDON_FILE.stem

_SCRIPT = r"""
import bpy, shutil, sys
src = sys.argv[sys.argv.index("--") + 1]
dst = bpy.utils.user_resource("SCRIPTS", path="addons", create=True)
shutil.copy(src, dst)
bpy.ops.preferences.addon_refresh()
bpy.ops.preferences.addon_enable(module="{module}")
bpy.ops.wm.save_userpref()
print("GM2B: plugin installed to", dst)
"""


def addon_installed() -> bool:
    """Есть ли плагин в папке аддонов хоть одной версии Blender пользователя."""
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender"
    else:
        root = Path.home() / ".config" / "blender"
    return any(root.glob(f"*/scripts/addons/{MODULE}.py"))


def install_addon(blender_exe: str) -> str:
    """Копирует плагин в папку аддонов пользователя, включает его и сохраняет настройки Blender."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(
        [blender_exe, "--background", "--python-exit-code", "1",
         "--python-expr", _SCRIPT.format(module=MODULE), "--", str(ADDON_FILE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags,
    )
    out = proc.stdout + proc.stderr
    line = next((l for l in out.splitlines() if l.startswith("GM2B:")), None)
    if proc.returncode != 0 or line is None:
        raise RuntimeError("Не удалось установить плагин:\n" + "\n".join(out.splitlines()[-15:]))
    return line[5:].strip()
