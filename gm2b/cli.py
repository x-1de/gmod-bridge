"""Консольный режим без интерфейса — удобно для отладки.

    python -m gm2b.cli addon.gma                      # список моделей
    python -m gm2b.cli addon.gma models/foo/bar.mdl   # экспорт в .blend
"""
import argparse
import sys
from pathlib import Path

from .exporter import ExportOptions, export_model, find_blender
from .sources import FolderSource, GmaSource, VirtualFS, find_gmod_dir, game_sources
from .studio import SourceModel
from .map_exporter import export_map
from . import __version__


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="GMod → Blender")
    ap.add_argument("addon", help=".gma/.bin или папка аддона")
    ap.add_argument("model", nargs="?", help="путь к .mdl внутри аддона")
    ap.add_argument("--out", default="out")
    ap.add_argument("--blender", default=find_blender())
    ap.add_argument("--scale", type=float, default=0.0254)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--format", dest="formats", action="append", choices=("blend", "fbx", "glb", "obj"))
    ap.add_argument("--gmod", type=Path, help="Папка Garry's Mod (рядом с bin и garrysmod)")
    ap.add_argument("--skin", type=int, default=0)
    ap.add_argument("--max-texture", type=int, default=1024)
    args = ap.parse_args()

    p = Path(args.addon)
    addon = FolderSource(p) if p.is_dir() else GmaSource(p)
    vfs = VirtualFS([addon, *game_sources(args.gmod or find_gmod_dir())])

    if not args.model:
        print(f"{addon.title}:")
        for m in addon.maps():
            print(f"  {m} [карта]")
        for m in addon.models():
            try:
                s = SourceModel.load(vfs, m).stats()
                print(f"  {m}  [костей {s['bones']}, треугольников {s['triangles']}]")
            except Exception as e:  # noqa: BLE001
                print(f"  {m}  ОШИБКА: {e}")
        return

    if not args.blender:
        ap.error("Blender не найден, укажите --blender")
    opts = ExportOptions(str(Path(args.out).resolve()), args.blender, args.scale,
                         skin=args.skin, max_texture=args.max_texture,
                         formats=tuple(args.formats or ["blend"]))
    export = export_map if args.model.lower().endswith(".bsp") else export_model
    export(vfs, args.model.lower(), opts, print)


if __name__ == "__main__":
    main()
