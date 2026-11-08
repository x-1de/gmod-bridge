"""Своя модель -> готовый аддон Garry's Mod.

  модель (FBX/OBJ/glTF/BLEND/STL/PLY)
    → Blender: ref.smd + phys.smd + текстуры        (blender_to_source.py)
    → VTF/VMT + QC                                  (здесь)
    → studiomdl.exe из папки GMod: MDL/VVD/VTX/PHY
    → папка аддона с addon.json → garrysmod/addons и/или .gma (gmad.exe)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from .materials import write_vtf
from .storage import install_directory, preserve_existing

Log = Callable[[str], None]
SCRIPT = Path(__file__).with_name("blender_to_source.py")

SURFACES = {
    "Металл": "metal", "Дерево": "wood", "Пластик": "plastic", "Бетон": "concrete",
    "Стекло": "glass", "Резина": "rubber", "Ткань": "carpet", "Плоть": "flesh", "Камень": "rock",
}
INPUT_EXTENSIONS = (".fbx", ".obj", ".glb", ".gltf", ".blend", ".stl", ".ply")
ADDON_TAGS = ("build", "fun", "realism", "scenic", "roleplay", "cartoon", "movie", "comic", "water")


@dataclass
class AddonOptions:
    input: str
    title: str
    out_dir: str
    blender_exe: str
    gmod_dir: str
    author: str = "gm2b"
    surface: str = "metal"
    mass: float = 20.0
    scale: float = 1 / 0.0254   # метры Blender -> дюймы Source
    rotate_deg: float = 0.0
    tags: tuple = ("build",)
    install: bool = True        # положить в garrysmod/addons
    make_gma: bool = True       # упаковать .gma для Workshop
    max_texture: int = 2048


@dataclass
class AddonResult:
    folder: Path
    model_path: str
    gma: Path | None = None
    installed: Path | None = None
    stats: dict = field(default_factory=dict)


def slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    return out or "model"


def _run(cmd, log: Log, cwd=None) -> str:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=flags)
    out = proc.stdout + proc.stderr
    for line in out.splitlines():
        if line.startswith("GM2B:"):
            log("  " + line[5:].strip())
    if proc.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} завершился с ошибкой:\n" + "\n".join(out.splitlines()[-20:]))
    return out


def _vmt(base: str, normal: str | None, alpha: str, surface: str) -> str:
    lines = ['"VertexLitGeneric"', "{", f'\t"$basetexture" "{base}"']
    if normal:
        lines.append(f'\t"$bumpmap" "{normal}"')
    if alpha == "CLIP":
        lines.append('\t"$alphatest" "1"')
    elif alpha == "BLEND":
        lines.append('\t"$translucent" "1"')
    lines.append(f'\t"$surfaceprop" "{surface}"')
    lines.append("}")
    return "\n".join(lines) + "\n"


def build_addon(opts: AddonOptions, log: Log) -> AddonResult:
    gmod = Path(opts.gmod_dir)
    studiomdl = gmod / "bin" / "studiomdl.exe"
    if not studiomdl.exists():
        raise RuntimeError(f"Не найден компилятор моделей: {studiomdl}")

    name = slug(opts.title)
    author = slug(opts.author)
    model_rel = f"{author}/{name}.mdl"                 # путь внутри models/
    mat_rel = f"models/{author}/{name}"                # путь внутри materials/
    folder = Path(opts.out_dir) / f"{name}_addon"
    backup = preserve_existing(folder)
    if backup:
        log(f"  предыдущая сборка сохранена: {backup}")
    log(f"▶ аддон «{opts.title}» из {Path(opts.input).name}")

    with tempfile.TemporaryDirectory(prefix="gm2b_") as tmp:
        work = Path(tmp)
        src = work / "src"
        src.mkdir()

        # 1. Blender: исходники SMD и текстуры
        job = {"input": str(Path(opts.input).resolve()), "out_dir": str(src),
               "scale": opts.scale, "rotate": __import__("math").radians(opts.rotate_deg)}
        (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
        log("  разбор модели в Blender…")
        _run([opts.blender_exe, "--background", "--factory-startup", "--python-exit-code", "1",
              "--python", str(SCRIPT), "--", str(work / "job.json")], log)
        info = json.loads((src / "source.json").read_text(encoding="utf-8"))

        # 2. Текстуры -> VTF, материалы -> VMT
        mat_out = folder / "materials" / mat_rel
        mat_out.mkdir(parents=True)
        for m in info["materials"]:
            if m["base"] and Path(m["base"]).exists():
                img = Image.open(m["base"])
            else:  # материал без картинки — заливка его цветом
                img = Image.new("RGB", (4, 4), tuple(int(max(0, min(1, c)) ** (1 / 2.2) * 255) for c in m["color"]))
            (mat_out / f"{m['name']}.vtf").write_bytes(write_vtf(img, max_size=opts.max_texture))
            normal = None
            if m["normal"] and Path(m["normal"]).exists():
                # Blender хранит нормали в OpenGL-формате, Source ждёт DirectX: переворачиваем зелёный
                nimg = Image.open(m["normal"]).convert("RGB")
                r, g, b = nimg.split()
                nimg = Image.merge("RGB", (r, g.point(lambda v: 255 - v), b))
                (mat_out / f"{m['name']}_normal.vtf").write_bytes(
                    write_vtf(nimg, normal=True, max_size=opts.max_texture))
                normal = f"{mat_rel}/{m['name']}_normal"
            (mat_out / f"{m['name']}.vmt").write_text(
                _vmt(f"{mat_rel}/{m['name']}", normal, m["alpha"], opts.surface), encoding="utf-8")
        log(f"  материалов: {len(info['materials'])}, треугольников: {info['triangles']}")

        # 3. QC + компиляция во временную «игру» (не трогаем папку garrysmod)
        qc = src / "model.qc"
        qc.write_text("\n".join([
            f'$modelname "{model_rel}"',
            "$staticprop",
            '$body "body" "ref.smd"',
            f'$surfaceprop "{opts.surface}"',
            f'$cdmaterials "{mat_rel}/"',
            '$sequence "idle" "ref.smd" fps 30',
            '$collisionmodel "phys.smd"',
            "{",
            f"\t$mass {opts.mass:g}",
            "}",
        ]) + "\n", encoding="utf-8")
        game = work / "game"
        game.mkdir()
        shutil.copy(gmod / "garrysmod" / "gameinfo.txt", game / "gameinfo.txt")
        log("  компиляция studiomdl…")
        out = _run([str(studiomdl), "-game", str(game), "-nop4", "-nox360", str(qc)], log, cwd=studiomdl.parent)
        compiled = game / "models" / Path(model_rel).parent
        files = sorted(compiled.glob(f"{name}.*"))
        if not any(f.suffix == ".mdl" for f in files):
            raise RuntimeError("studiomdl не создал .mdl:\n" + "\n".join(out.splitlines()[-25:]))
        models_out = folder / "models" / Path(model_rel).parent
        models_out.mkdir(parents=True)
        for f in files:
            shutil.copy(f, models_out / f.name)
        log("  скомпилировано: " + ", ".join(f.name for f in files))

    # 4. addon.json, установка, .gma
    (folder / "addon.json").write_text(json.dumps({
        "title": opts.title, "type": "model", "tags": list(opts.tags)[:2] or ["build"], "ignore": [],
    }, ensure_ascii=False, indent="\t"), encoding="utf-8")
    result = AddonResult(folder, f"models/{model_rel}", stats=info)

    if opts.make_gma:
        gma = Path(opts.out_dir) / f"{name}.gma"
        _run([str(gmod / "bin" / "gmad.exe"), "create", "-folder", str(folder), "-out", str(gma)], log)
        result.gma = gma
        log(f"  .gma для Workshop: {gma}")
    if opts.install:
        target = gmod / "garrysmod" / "addons" / f"gm2b_{name}"
        install_directory(folder, target)
        result.installed = target
        log(f"  установлено в игру: {target}")
    log(f"✔ готово: {result.model_path}")
    return result
