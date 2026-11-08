"""Экспорт карты: BSP -> job.json + job.bin (большие массивы) + PNG -> Blender -> .blend.

Пропы не копируются на каждое место установки: каждая модель (с учётом скина)
импортируется один раз в библиотечную коллекцию, а на карте стоят её экземпляры
(collection instance). Так карта с тысячами пропов остаётся лёгкой.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from .bsp import Bsp, MeshArrays
from .exporter import (
    ExportOptions, Log, TextureCache, finish_export, material_dict, run_blender, safe_name,
)
from PIL import Image

from .materials import read_vtf, resolve_material, resolve_world_material
from .sources import VirtualFS, ZipSource
from .studio import SourceModel

BUILD_SCRIPT = Path(__file__).with_name("blender_build_map.py")


class BlobWriter:
    """Пишет numpy-массивы подряд в один .bin, возвращает ссылки для JSON."""

    def __init__(self, path: Path):
        self.f = open(path, "wb")
        self.offset = 0

    def add(self, arr) -> dict:
        arr = np.ascontiguousarray(arr)
        self.f.write(arr.tobytes())
        ref = {"off": self.offset, "n": int(arr.size), "dtype": arr.dtype.str}
        self.offset += arr.nbytes
        return ref

    def close(self):
        self.f.close()


class MaterialRegistry:
    """Общий список материалов карты. Материалы с одинаковым содержимым (те же текстуры
    и режимы) сливаются в один: карты хранят десятки копий одного материала под разными
    именами (например, патчи под env_cubemap), а каждый материал — это шейдер в Blender."""

    def __init__(self):
        self.index: dict[tuple, int] = {}
        self.infos = []

    def add(self, info) -> int:
        if info.base_texture or info.color or info.water:
            key = (info.base_texture, info.base_texture2, info.normal_texture,
                   info.alpha, info.nocull, info.water, info.color)
        else:
            key = ("missing", info.name)
        if key not in self.index:
            self.index[key] = len(self.infos)
            self.infos.append(info)
        return self.index[key]


def _mesh_ref(blobs: BlobWriter, name: str, m: MeshArrays, mat_map) -> dict:
    return {
        "name": name,
        "pos": blobs.add(m.positions.astype(np.float32)),
        "loops": blobs.add(m.loop_verts.astype(np.int32)),
        "starts": blobs.add(m.face_starts.astype(np.int32)),
        "sizes": blobs.add(m.face_sizes.astype(np.int32)),
        "uvs": blobs.add(m.uvs.astype(np.float32)),
        "mats": blobs.add(mat_map[m.face_materials].astype(np.int32)),
        "smooth": blobs.add(m.face_smooth.astype(np.uint8)),
        "blend": blobs.add(m.blend.astype(np.float32)) if m.blend.any() else None,
    }


def _prop_mesh(model: SourceModel, skin: int, vfs, registry: MaterialRegistry, mat_cache: dict):
    """Статичная геометрия модели (первый вариант каждого bodygroup) без костей."""
    mdl, verts, vtx = model.mdl, model.vvd.vertices, model.vtx
    family = mdl.skins[skin] if skin < len(mdl.skins) else mdl.skins[0]
    faces, face_mats = [], []
    for bpi, bp in enumerate(mdl.bodyparts):
        if not bp.models:
            continue
        m = bp.models[0]
        for mei, mesh in enumerate(m.meshes):
            base = m.vertex_index + mesh.vertex_offset
            tex = family[mesh.material] if mesh.material < len(family) else 0
            key = (model.path, tex)
            if key not in mat_cache:
                mat_cache[key] = registry.add(resolve_material(vfs, mdl, tex)) if tex < len(mdl.textures) else 0
            for a, b, c in vtx.meshes[bpi][0][mei]:
                faces.append((base + a, base + c, base + b))
                face_mats.append(mat_cache[key])
    if not faces:
        return None
    f = np.array(faces, np.int64)
    used, inv = np.unique(f, return_inverse=True)
    v = np.array([verts[i] for i in used], np.float32)
    n_faces = len(f)
    return {
        "positions": v[:, 7:10], "normals": v[:, 10:13],
        "uvs_vertex": np.stack([v[:, 13], 1.0 - v[:, 14]], axis=1),
        "loops": inv.reshape(-1).astype(np.int32),
        "starts": (np.arange(n_faces) * 3).astype(np.int32),
        "sizes": np.full(n_faces, 3, np.int32),
        "mats": np.array(face_mats, np.int32),
    }


def _sky_color(vfs, bsp: Bsp) -> list[float]:
    """Цвет фона сцены = средний цвет боковых граней неба карты (worldspawn.skyname).
    Без него окна и проёмы в небо выглядят как серые дыры."""
    world = next((e for e in bsp.entities if e.get("classname") == "worldspawn"), {})
    sky = world.get("skyname", "").lower()
    colors = []
    for side in ("ft", "bk", "lf", "rt"):
        info = resolve_world_material(vfs, f"skybox/{sky}{side}")
        if not info.base_texture:
            continue
        try:
            img = read_vtf(vfs.read(info.base_texture)).convert("RGB")
            # нижняя половина боковой грани — горизонт и земля, берём верхнюю
            img = img.crop((0, 0, img.width, img.height // 2)).resize((1, 1), Image.Resampling.BOX)
            colors.append(img.getpixel((0, 0)))
        except (ValueError, OSError, KeyError):
            continue
    if not colors:
        return [0.45, 0.6, 0.8]
    # sRGB -> линейный цвет для Blender
    return [((sum(c[i] for c in colors) / len(colors)) / 255.0) ** 2.2 for i in range(3)]


def export_map(vfs: VirtualFS, bsp_path: str, opts: ExportOptions, log: Log,
               import_props: bool = True, import_lights: bool = True) -> Path:
    t0 = time.time()
    log(f"▶ {bsp_path}")
    bsp = Bsp(vfs.read(bsp_path))
    vfs = VirtualFS([ZipSource(bsp.pakfile, "pakfile карты"), *vfs.sources])
    name = safe_name(Path(bsp_path).stem)
    out = Path(opts.out_dir) / name
    tex_dir = out / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)
    registry = MaterialRegistry()

    # --- браши: мир и брашевые энтити
    world_mats = np.array([registry.add(resolve_world_material(vfs, t)) for t in bsp.texture_names], np.int32)
    blobs = BlobWriter(out / "job.bin")
    # 3D-скайбокс — уменьшенная копия окружения в отдельной зоне карты; выносим отдельно
    sky_area = bsp.skybox_area()
    sky_faces = bsp.skybox_faces()
    world = bsp.model_mesh(0, exclude=sky_faces)
    log(f"  мир: {len(world.face_sizes)} граней, {len(bsp.dispinfo) // 176} displacement")
    job = {
        "name": name, "scale": opts.scale, "world": _mesh_ref(blobs, "world", world, world_mats),
        "skybox": None, "brushes": [], "prop_lib": [], "props": [], "lights": [],
    }
    if sky_faces:
        sky = bsp.model_mesh(0, only=sky_faces)
        if not sky.empty:
            job["skybox"] = _mesh_ref(blobs, "skybox_3d", sky, world_mats)
            log(f"  3D-скайбокс: {len(sky.face_sizes)} граней (отдельная скрытая коллекция)")
    for be in bsp.brush_entities():
        m = bsp.model_mesh(be.model)
        if not m.empty:
            job["brushes"].append({**_mesh_ref(blobs, f"{be.classname}_{be.model}", m, world_mats),
                                   "origin": be.origin, "angles": be.angles})
    log(f"  брашевых объектов: {len(job['brushes'])}")

    # --- пропы: библиотека уникальных (модель, скин) + экземпляры
    if import_props:
        placements = bsp.static_props() + bsp.entity_props()
        lib_index: dict[tuple[str, int], int] = {}
        models: dict[str, SourceModel | None] = {}
        mat_cache: dict = {}
        missing = set()
        for p in placements:
            key = (p.model, p.skin)
            if key not in lib_index:
                if p.model not in models:
                    try:
                        models[p.model] = SourceModel.load(vfs, p.model)
                    except Exception:  # noqa: BLE001 — модели нет в установленном контенте
                        models[p.model] = None
                        missing.add(p.model)
                model = models[p.model]
                mesh = _prop_mesh(model, p.skin, vfs, registry, mat_cache) if model else None
                lib_index[key] = len(job["prop_lib"]) if mesh else -1
                if mesh:
                    job["prop_lib"].append({
                        "name": f"{Path(p.model).stem}" + (f"_skin{p.skin}" if p.skin else ""),
                        **{k: blobs.add(v) for k, v in mesh.items()},
                    })
            if lib_index[key] >= 0:
                sky = 1 if bsp.in_area(p.origin, sky_area) else 0
                job["props"].append([lib_index[key], *p.origin, *p.angles, p.scale, sky])
        log(f"  пропов: {len(job['props'])} (уникальных {len(job['prop_lib'])}), "
            f"не найдено моделей: {len(missing)}")
        for m in sorted(missing)[:15]:
            log(f"    ⚠ нет модели {m}")

    if import_lights:
        job["lights"] = [l.__dict__ for l in bsp.lights()]
        log(f"  источников света: {len(job['lights'])}")
    blobs.close()
    job["sky_color"] = _sky_color(vfs, bsp)

    # --- материалы и текстуры (параллельно)
    textures = TextureCache(vfs, tex_dir, log, opts.max_texture)
    paths = [p for i in registry.infos for p in (i.base_texture, i.base_texture2, i.normal_texture)]
    log(f"  текстур: {len({p for p in paths if p})} — конвертация…")
    textures.export_many(paths, normals=[i.normal_texture for i in registry.infos])
    job["materials"] = [material_dict(i, textures) for i in registry.infos]
    missing_mats = sum(1 for i in registry.infos if not i.found and not i.water)
    log(f"  материалов: {len(job['materials'])}, без текстуры: {missing_mats}")

    job["output"] = str(out / f"{name}.blend")
    job["bin"] = str(out / "job.bin")
    job["formats"] = list(opts.formats)
    job_path = out / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    log("  сборка сцены в Blender…")
    run_blender(opts.blender_exe, job_path, log, script=BUILD_SCRIPT)
    job_path.unlink(missing_ok=True)
    (out / "job.bin").unlink(missing_ok=True)
    if "blend" not in opts.formats:
        shutil.rmtree(tex_dir, ignore_errors=True)
    log(f"  время: {time.time() - t0:.0f} с")
    return finish_export(out, name, opts, log)
