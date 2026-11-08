# Contributing

Python 3.11+, Windows 10/11 x64. Install with `python -m pip install -e ".[dev]"`.

Run before opening a pull request:

```powershell
python -m ruff check gm2b tests scripts
python -m unittest discover -s tests -v
python scripts/smoke_gui.py
python -m build
```

Blender-side modules import `bpy` and run inside Blender, not ordinary Python.
For changes to rigging, inspect the exported model in GMod: standing, walking,
crouching, pistol/SMG/crowbar holds and ragdoll. A successful compile is insufficient.
Report the Blender version, input format, steps, expected/actual result and log.
Use your own small test assets; do not commit game or Workshop content.

The Windows archive is built with `python scripts/build_release.py`. The GitHub
release workflow uploads build artifacts; publishing them is a separate action.

Keep input assets intact. Installation must preserve the previous addon if staging fails.
