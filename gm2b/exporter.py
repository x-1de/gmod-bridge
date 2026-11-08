"""Экспорт: модель Source -> job.json + PNG-текстуры -> Blender (фоново) -> .blend."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from .flexrules import compile_flex
from .materials import MaterialInfo, read_vtf, resolve_material
from .studio import SourceModel

Log = Callable[[str], None]

BUILD_SCRIPT = Path(__file__).with_name("blender_build.py")


@dataclass
class ExportOptions:
    out_dir: str
    blender_exe: str
    scale: float = 0.0254  # дюймы Source -> метры Blender
    skin: int = 0          # скин, включённый по умолчанию
    max_texture: int = 0   # ограничение стороны текстур карт (0 — оригинал)
    formats: tuple = ("blend",)  # blend / fbx / glb / obj
    pack_textures: bool = True
    open_after: bool = False

    def __post_init__(self):
        import math
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("Масштаб должен быть положительным числом")
        if not self.formats or any(f not in {"blend", "fbx", "glb", "obj"} for f in self.formats):
            raise ValueError("Выберите форматы blend, fbx, glb или obj")
        if self.max_texture < 0 or self.skin < 0:
            raise ValueError("Размер текстур и номер скина не могут быть отрицательными")


def safe_name(s: str) -> str:
    return re.sub(r"[^\w\-.]+", "_", s).strip("_") or "model"


def find_blender() -> str | None:
    candidates: list[Path] = []
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            root = Path(os.environ.get(env, "")) / "Blender Foundation"
            if root.is_dir():
                candidates += sorted(root.glob("*/blender.exe"), reverse=True)
        from .sources import steam_libraries

        for lib in steam_libraries():
            candidates.append(lib / "steamapps" / "common" / "Blender" / "blender.exe")
    which = shutil.which("blender")
    if which:
        candidates.append(Path(which))
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


class TextureCache:
    """VTF -> PNG, каждая текстура конвертируется один раз."""

    def __init__(self, vfs, tex_dir: Path, log: Log, max_size: int = 0):
        self.vfs, self.tex_dir, self.log = vfs, tex_dir, log
        self.max_size = max_size  # 0 — без ограничения
        self.done: dict[str, str | None] = {}
        self.normals: set[str] = set()  # карты нормалей: переводятся из DirectX в OpenGL

    def target(self, vtf_path: str) -> Path:
        return self.tex_dir / (safe_name(vtf_path[len("materials/"):-4].replace("/", "_")) + ".png")

    def export(self, vtf_path: str | None, normal: bool = False) -> str | None:
        if not vtf_path:
            return None
        if normal:
            self.normals.add(vtf_path)
        if vtf_path not in self.done:
            try:
                out = self.target(vtf_path)
                img = read_vtf(self.vfs.read(vtf_path))
                if self.max_size and max(img.size) > self.max_size:
                    img.thumbnail((self.max_size, self.max_size), Image.Resampling.LANCZOS)
                if vtf_path in self.normals:
                    img = flip_green(img)
                img.save(out, compress_level=1)
                self.done[vtf_path] = str(out)
            except (ValueError, OSError, KeyError) as e:
                self.log(f"  ⚠ текстура {vtf_path}: {e}")
                self.done[vtf_path] = None
        return self.done[vtf_path]

    def export_many(self, paths, normals=(), workers: int = 8):
        """Параллельная конвертация (Pillow отпускает GIL на декодировании и PNG)."""
        from concurrent.futures import ThreadPoolExecutor

        self.normals.update(p for p in normals if p)
        todo = sorted({p for p in paths if p and p not in self.done})
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(self.export, todo))


def flip_green(img: Image.Image) -> Image.Image:
    """Карта нормалей Source (DirectX, Y вниз) -> OpenGL (Y вверх), как ждут Blender, FBX и glTF."""
    bands = list(img.convert("RGBA" if "A" in img.getbands() else "RGB").split())
    bands[1] = bands[1].point(lambda v: 255 - v)
    return Image.merge("RGBA" if len(bands) == 4 else "RGB", bands)


def material_dict(info: MaterialInfo, textures: TextureCache) -> dict:
    return {
        "name": info.name.split("/")[-1],
        "shader": info.shader,
        "base": textures.export(info.base_texture),
        "base2": textures.export(info.base_texture2),
        "normal": textures.export(info.normal_texture, normal=True),
        "alpha": info.alpha,
        "nocull": info.nocull,
        "water": info.water,
        "color": info.color,
    }


def build_job(vfs, model: SourceModel, tex_dir: Path, log: Log) -> dict:
    mdl, vvd, vtx = model.mdl, model.vvd, model.vtx
    verts = vvd.vertices

    # --- материалы и текстуры
    textures = TextureCache(vfs, tex_dir, log)
    materials = []
    for i in range(len(mdl.textures)):
        info: MaterialInfo = resolve_material(vfs, mdl, i)
        if not info.vmt_path:
            log(f"  ⚠ материал не найден: {info.name}")
        elif not info.base_texture:
            log(f"  ⚠ у материала {info.name} не найдена текстура")
        materials.append(material_dict(info, textures))

    # --- скины: слот материала (skinref) -> индекс текстуры в каждом семействе
    num_slots = len(mdl.skins[0])
    slots = [[fam[ref] for fam in mdl.skins] for ref in range(num_slots)]

    # --- геометрия: каждая подмодель bodygroup'а -> отдельный меш
    bodyparts = []
    for bpi, bp in enumerate(mdl.bodyparts):
        bp_out = {"name": bp.name, "models": []}
        for mi, m in enumerate(bp.models):
            faces_global: list[tuple[int, int, int]] = []
            face_mats: list[int] = []
            for mei, mesh in enumerate(m.meshes):
                base = m.vertex_index + mesh.vertex_offset
                for a, b, c in vtx.meshes[bpi][mi][mei]:
                    # Source — обход по часовой, Blender — против
                    faces_global.append((base + a, base + c, base + b))
                    face_mats.append(mesh.material if mesh.material < num_slots else 0)
            if not faces_global:
                bp_out["models"].append({"name": m.name or "blank", "empty": True})
                continue

            used = sorted({i for f in faces_global for i in f})
            remap = {g: n for n, g in enumerate(used)}
            positions, normals, uvs, weights = [], [], [], []
            for g in used:
                v = verts[g]
                positions += v[7:10]
                normals += v[10:13]
                uvs += v[13:15]
                weights.append([[v[3 + k], round(v[k], 5)] for k in range(v[6]) if v[k] > 0])
            bp_out["models"].append({
                "name": m.name or f"{bp.name}_{mi}",
                "positions": positions,
                "normals": normals,
                "uvs": uvs,
                "faces": [remap[i] for f in faces_global for i in f],
                "face_materials": face_mats,
                "weights": weights,
                "shape_keys": _shape_keys(mdl, m, remap),
            })
        bodyparts.append(bp_out)

    return {
        "name": Path(model.path).stem,
        "source_path": model.path,
        "bones": [{"name": b.name, "parent": b.parent, "pos": b.pos, "quat": b.quat} for b in mdl.bones],
        "materials": materials,
        "slots": slots,
        "bodyparts": bodyparts,
        "flex": compile_flex(mdl),
    }


def _shape_keys(mdl, model, remap: dict[int, int]) -> list[dict]:
    """Flex -> shape keys. Дельты от нескольких мешей и flex'ов складываются по имени.
    Стерео-flex делится между правым (desc) и левым (pair) ключом по байту side."""
    keys: dict[int, dict[int, list[float]]] = {}

    def add(desc: int, vi: int, w: float, dx: float, dy: float, dz: float):
        if w <= 0 or vi not in remap:
            return
        d = keys.setdefault(desc, {}).setdefault(remap[vi], [0.0, 0.0, 0.0])
        d[0] += dx * w
        d[1] += dy * w
        d[2] += dz * w

    for mesh in model.meshes:
        base = model.vertex_index + mesh.vertex_offset
        for flex in mesh.flexes:
            for idx, side, dx, dy, dz in flex.verts:
                if idx >= mesh.num_vertices:
                    continue
                if flex.pair:
                    s = side / 255.0
                    add(flex.desc, base + idx, 1.0 - s, dx, dy, dz)
                    add(flex.pair, base + idx, s, dx, dy, dz)
                else:
                    add(flex.desc, base + idx, 1.0, dx, dy, dz)

    out = []
    for desc in sorted(keys):
        verts = keys[desc]
        name = mdl.flex_names[desc] if desc < len(mdl.flex_names) else f"flex_{desc}"
        out.append({
            "name": name,
            "indices": list(verts),
            "deltas": [round(c, 5) for d in verts.values() for c in d],
        })
    return out


def run_blender(blender_exe: str, job_path: Path, log: Log, script: Path = BUILD_SCRIPT) -> None:
    cmd = [blender_exe, "--background", "--factory-startup", "--python-exit-code", "1",
           "--python", str(script), "--", str(job_path)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags)
    tail: list[str] = []
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-15:]
        if line.startswith("GM2B:"):
            log("  " + line[5:].strip())
    if proc.wait() != 0:
        raise RuntimeError("Blender завершился с ошибкой:\n" + "\n".join(tail))


def export_model(vfs, mdl_path: str, opts: ExportOptions, log: Log) -> Path:
    log(f"▶ {mdl_path}")
    model = SourceModel.load(vfs, mdl_path)
    s = model.stats()
    log(f"  костей: {s['bones']}, треугольников: {s['triangles']}, bodygroups: {s['bodygroups']}, "
        f"скинов: {s['skins']}, flex: {s['flexes']}")

    name = safe_name(Path(mdl_path).stem)
    out = Path(opts.out_dir) / name
    tex_dir = out / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    job = build_job(vfs, model, tex_dir, log)
    job["scale"] = opts.scale
    job["skin"] = min(opts.skin, len(job["slots"][0]) - 1) if job["slots"] else 0
    job["pack_textures"] = opts.pack_textures
    job["formats"] = list(opts.formats)
    job["output"] = str(out / f"{name}.blend")
    job_path = out / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    log("  сборка сцены в Blender…")
    run_blender(opts.blender_exe, job_path, log)
    # FBX и GLB хранят текстуры внутри, OBJ копирует их к себе — папка нужна только .blend без упаковки
    if opts.pack_textures or "blend" not in opts.formats:
        shutil.rmtree(tex_dir, ignore_errors=True)
    job_path.unlink(missing_ok=True)
    return finish_export(out, name, opts, log)


def finish_export(out: Path, name: str, opts: ExportOptions, log: Log) -> Path:
    files = [out / f"{name}.{f}" for f in opts.formats]
    log(f"✔ готово: {', '.join(p.name for p in files if p.exists())}  →  {out}")
    missing = [p.name for p in files if not p.exists()]
    if missing:
        raise RuntimeError(f"не созданы файлы: {', '.join(missing)}")
    if opts.open_after and "blend" in opts.formats:
        subprocess.Popen([opts.blender_exe, str(out / f"{name}.blend")])
    return out
