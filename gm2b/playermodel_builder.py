"""Свой персонаж -> playermodel для Garry's Mod (мастер в стиле Mixamo).

  1. prepare(): Blender собирает модель в один меш, ставит на пол, подгоняет рост,
     рендерит виды спереди/сбоку и угадывает точки (подбородок, запястья, локти, колени, пах)
  2. rig():     по точкам строится скелет ValveBiped + автоматические веса + тестовая поза
  3. build():   QC с анимациями игрока и «proportion trick», рэгдолл, studiomdl,
                Lua-регистрация в меню выбора модели, addon.json, установка и .gma
"""
from __future__ import annotations

import json
import math
import re
import shutil
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .addon_builder import Log, _run, _vmt, slug
from .materials import write_vtf
from .sources import VirtualFS, game_sources
from .studio import Mdl
from .storage import install_directory, preserve_existing

HERE = Path(__file__).parent
PREPARE = HERE / "blender_char_prepare.py"
RIG = HERE / "blender_char_rig.py"
STANDARD_HEIGHT_IN = 72.0  # рост стандартного игрока в юнитах Source
SKELETON_BONES = 26        # первые 26 костей эталона: тело + вспомогательные
SKELETON_WITH_FINGERS = 56  # + по 15 костей пальцев на руку
SKELETON_WITH_PALMS = 64    # + четыре кости пясти на каждую руку

GENDERS = {
    "male": {"anims": "m_anm.mdl", "phy": "models/humans/group01/male_07.phy", "hands": "models/weapons/c_arms_citizen.mdl"},
    "female": {"anims": "f_anm.mdl", "phy": "models/humans/group01/female_01.phy",
               "hands": "models/weapons/c_arms_citizen.mdl"},
}


@dataclass
class CharacterSession:
    """Рабочая папка одного персонажа: результаты шагов мастера."""
    input: str
    work: Path
    prepare: dict = field(default_factory=dict)
    rig: dict = field(default_factory=dict)


@dataclass
class PlayermodelOptions:
    title: str
    out_dir: str
    gmod_dir: str
    gender: str = "male"
    author: str = "gm2b"
    install: bool = True
    make_gma: bool = True
    max_texture: int = 2048


def _blender(exe: str, script: Path, job: dict, work: Path, log: Log):
    path = work / f"{script.stem}_job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    _run([exe, "--background", "--factory-startup", "--python-exit-code", "1",
          "--python", str(script), "--", str(path)], log)


def standard_skeleton(gmod_dir, gender: str, count: int = SKELETON_BONES) -> list[dict]:
    """Эталонный скелет ValveBiped из анимаций игрока (m_anm / f_anm)."""
    vfs = VirtualFS(game_sources(Path(gmod_dir)))
    mdl = Mdl(vfs.read("models/" + GENDERS[gender]["anims"]))
    bones = [{"name": b.name, "parent": b.parent, "pos": list(b.pos), "quat": list(b.quat)}
             for b in mdl.bones[:min(count, SKELETON_WITH_FINGERS)]]
    if count > SKELETON_WITH_FINGERS:
        for side in ("R", "L"):
            hand = next(i for i, b in enumerate(bones) if b["name"] == f"ValveBiped.Bip01_{side}_Hand")
            for finger in range(1, 5):
                root = next(b for b in bones if b["name"] == f"ValveBiped.Bip01_{side}_Finger{finger}")
                # Independent children of Hand: the animation hierarchy stays unchanged.
                bones.append({"name": f"ValveBiped.Bip01_{side}_Palm{finger}", "parent": hand,
                              "pos": [v * 0.3 for v in root["pos"]], "quat": [0, 0, 0, 1],
                              "palm_tip": root["name"]})
    return bones


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tga", ".bmp", ".dds", ".tif", ".tiff", ".webp")
_NORMAL_HINTS = ("normal", "nrm", "norm", "_n", "bump", "nm")
_STRIP = ("basecolor", "base_color", "diffuse", "albedo", "color", "colour", "diff", "_col", "_tex",
          "_d", "_c", "_bc", "texture")


def _texture_key(stem: str) -> str:
    s = stem.lower()
    for hint in _STRIP + _NORMAL_HINTS:
        if s.endswith(hint):
            s = s[: -len(hint)]
    return re.sub(r"[^a-z0-9]+", "", s)


def match_textures(folder: str, material_names: list[str]) -> dict:
    """Подбор текстур в папке по именам материалов: body.png / body_diffuse.png / Body_N.png…

    Возвращает {материал: {"base": путь, "normal": путь}} для найденных."""
    from difflib import SequenceMatcher

    files = [p for p in Path(folder).rglob("*") if p.suffix.lower() in IMAGE_EXT]
    result = {}
    for name in material_names:
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        best = {"base": (0.0, None), "normal": (0.0, None)}
        for f in files:
            stem = f.stem.lower()
            kind = "normal" if any(stem.endswith(h) or f"_{h}_" in stem for h in _NORMAL_HINTS) else "base"
            fk = _texture_key(f.stem)
            score = 1.0 if fk == key else SequenceMatcher(None, fk, key).ratio()
            if key and (key in fk or fk in key):
                score = max(score, 0.85)
            if score > best[kind][0]:
                best[kind] = (score, f)
        found = {k: str(v[1]) for k, v in best.items() if v[1] is not None and v[0] >= 0.75}
        if found:
            result[name] = found
    return result


def ragdoll_template(gmod_dir, gender: str) -> tuple[list[str], dict, list[dict]]:
    """Части рэгдолла, массы и ограничения суставов из готового персонажа HL2 (.phy)."""
    vfs = VirtualFS(game_sources(Path(gmod_dir)))
    data = vfs.read(GENDERS[gender]["phy"])
    size, _id, solid_count, _chk = struct.unpack_from("<4i", data, 0)
    off = size
    for _ in range(solid_count):
        (n,) = struct.unpack_from("<i", data, off)
        off += 4 + n
    text = data[off:].split(b"\0")[0].decode("latin-1")
    blocks = re.findall(r"(\w+)\s*\{([^}]*)\}", text)
    solids, masses, constraints = [], {}, []
    names = {}
    for kind, body in blocks:
        kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', body))
        if kind == "solid":
            names[kv["index"]] = kv["name"]
            solids.append(kv["name"])
            masses[kv["name"]] = float(kv.get("mass", 1))
        elif kind == "ragdollconstraint":
            constraints.append(kv)
    joints = []
    for c in constraints:
        child = names.get(c.get("child"))
        if child:
            joints.append({"bone": child, **{k: float(c.get(k, 0)) for k in
                                              ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax",
                                               "xfriction", "yfriction", "zfriction")}})
    return solids, masses, joints


def prepare(input_path: str, blender: str, height_in: float, rotate: float, log: Log,
            work: Path | None = None) -> CharacterSession:
    work = Path(work) if work else Path.home() / "Documents" / "GMod2Blender" / "projects" / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    log(f"▶ персонаж из {Path(input_path).name}")
    _blender(blender, PREPARE, {"input": str(Path(input_path).resolve()), "out_dir": str(work),
                                "rotate": rotate, "height_m": height_in * 0.0254}, work, log)
    s = CharacterSession(input_path, work)
    s.prepare = json.loads((work / "prepare.json").read_text(encoding="utf-8"))
    return s


def rig(session: CharacterSession, markers: dict, blender: str, gmod_dir, gender: str, log: Log,
        fingers: bool = True, textures: dict | None = None, palms: bool = True):
    """markers: {точка: [x, z, y]} в метрах; y (глубина с вида сбоку) может быть None."""
    validate_markers(markers, session.prepare["height_m"])
    solids, _masses, _joints = ragdoll_template(gmod_dir, gender)
    count = (SKELETON_WITH_PALMS if palms else SKELETON_WITH_FINGERS) if fingers else SKELETON_BONES
    _blender(blender, RIG, {"out_dir": str(session.work), "height_m": session.prepare["height_m"],
                            "markers": markers, "bones": standard_skeleton(gmod_dir, gender, count),
                            "solids": solids, "textures": textures or {}}, session.work, log)
    session.rig = json.loads((session.work / "rig.json").read_text(encoding="utf-8"))
    return session


def validate_markers(markers: dict, height: float):
    """Reject invalid/overlapping joints before starting Blender, with actionable feedback."""
    if not math.isfinite(height) or height <= 0:
        raise ValueError("Рост персонажа должен быть больше нуля")
    required = {"groin", "chin"} | {f"{k}_{s}" for s in ("r", "l")
                                         for k in ("shoulder", "elbow", "wrist", "knee", "ankle")}
    missing = required - markers.keys()
    if missing:
        raise ValueError("Не заданы точки: " + ", ".join(sorted(missing)))
    for key, coords in markers.items():
        if len(coords) not in (2, 3) or any(
                not isinstance(v, (int, float)) or not math.isfinite(v)
                for i, v in enumerate(coords) if i < 2 or v is not None):
            raise ValueError(f"Недопустимые координаты точки {key}")
    for side in ("r", "l"):
        for a, b in (("shoulder", "elbow"), ("elbow", "wrist"), ("hip", "knee"),
                     ("knee", "ankle"), ("ankle", "toe"), ("wrist", "fingertip")):
            a, b = f"{a}_{side}", f"{b}_{side}"
            if a not in markers or b not in markers:
                continue
            aa, bb = markers[a], markers[b]
            axes = 3 if len(aa) == len(bb) == 3 and aa[2] is not None and bb[2] is not None else 2
            if math.dist(aa[:axes], bb[:axes]) < height * 0.002:
                raise ValueError(f"Точки {a} и {b} слишком близко — раздвиньте суставы")


def depth_at(session: CharacterSession, x: float, z: float, spread: float = 0.0) -> float:
    """Середина тела по глубине (y) в точке вида спереди — из карты глубины prepare().

    spread — полуширина горизонтальной полосы (м), по которой берётся медиана: для центральных
    точек (пах, подбородок), чтобы луч между ногами, попавший в прядь волос, не утащил сустав назад."""
    import numpy as np

    if not hasattr(session, "_depth"):
        session._depth = np.load(session.work / "depth.npy")
    grid = session._depth
    m, stride = session.prepare["front"], session.prepare["depth_stride"]
    gx = int((m["w"] / 2 + (x - m["cx"]) / m["upp"]) / stride)
    gy = int((m["h"] / 2 - (z - m["cz"]) / m["upp"]) / stride)
    sx = int(spread / m["upp"] / stride)
    h, w = grid.shape
    for r in range(0, 12):  # точка вне силуэта — ищем ближайшую заполненную клетку
        y0, y1 = max(gy - r, 0), min(gy + r + 1, h)
        x0, x1 = max(gx - r - sx, 0), min(gx + r + sx + 1, w)
        window = grid[y0:y1, x0:x1]
        if window.size and not np.isnan(window).all():
            return float(np.nanmedian(window))
    return 0.0


CENTER_MARKERS = {"chin", "groin"}


def default_depth(session: CharacterSession, key: str, x: float, z: float) -> float:
    spread = 0.06 * session.prepare["height_m"] if key in CENTER_MARKERS else 0.0
    return depth_at(session, x, z, spread)


def _reference_smd(bones: list[dict]) -> str:
    """Эталонный скелет как SMD — для «proportion trick»."""
    from math import atan2, asin

    def euler(q):
        x, y, z, w = q
        # кватернион -> углы XYZ (как Matrix.to_euler('XYZ') в Blender)
        r00 = 1 - 2 * (y * y + z * z)
        r10 = 2 * (x * y + z * w)
        r20 = 2 * (x * z - y * w)
        r21 = 2 * (y * z + x * w)
        r22 = 1 - 2 * (x * x + y * y)
        return atan2(r21, r22), asin(max(-1.0, min(1.0, -r20))), atan2(r10, r00)

    lines = ["version 1", "nodes"]
    lines += [f'{i} "{b["name"]}" {b["parent"]}' for i, b in enumerate(bones)]
    lines += ["end", "skeleton", "time 0"]
    for i, b in enumerate(bones):
        rx, ry, rz = euler(b["quat"])
        x, y, z = b["pos"]
        lines.append(f"{i} {x:.6f} {y:.6f} {z:.6f} {rx:.6f} {ry:.6f} {rz:.6f}")
    lines += ["end"]
    return "\n".join(lines) + "\n"


def build(session: CharacterSession, opts: PlayermodelOptions, log: Log):
    gmod = Path(opts.gmod_dir)
    studiomdl = gmod / "bin" / "studiomdl.exe"
    name = slug(opts.title)
    author = slug(opts.author)
    model_rel = f"player/{author}/{name}.mdl"
    mat_rel = f"models/player/{author}/{name}"
    folder = Path(opts.out_dir) / f"{name}_playermodel"
    backup = preserve_existing(folder)
    if backup:
        log(f"  предыдущая сборка сохранена: {backup}")
    work = session.work
    info = session.rig
    g = GENDERS[opts.gender]

    # 1. Текстуры и материалы
    mat_out = folder / "materials" / mat_rel
    mat_out.mkdir(parents=True)
    for m in info["materials"]:
        if m["base"] and Path(m["base"]).exists():
            img = Image.open(m["base"])
        else:
            img = Image.new("RGB", (4, 4), tuple(int(max(0, min(1, c)) ** (1 / 2.2) * 255) for c in m["color"]))
        (mat_out / f"{m['name']}.vtf").write_bytes(write_vtf(img, max_size=opts.max_texture))
        normal = None
        if m["normal"] and Path(m["normal"]).exists():
            r, gch, b = Image.open(m["normal"]).convert("RGB").split()
            nimg = Image.merge("RGB", (r, gch.point(lambda v: 255 - v), b))  # OpenGL -> DirectX
            (mat_out / f"{m['name']}_normal.vtf").write_bytes(write_vtf(nimg, normal=True, max_size=opts.max_texture))
            normal = f"{mat_rel}/{m['name']}_normal"
        (mat_out / f"{m['name']}.vmt").write_text(_vmt(f"{mat_rel}/{m['name']}", normal, m["alpha"], "flesh"),
                                                  encoding="utf-8")

    # 2. QC: анимации игрока, подгонка пропорций, рэгдолл
    bones = standard_skeleton(gmod, opts.gender, info.get("bone_count", SKELETON_BONES))
    (work / "reference.smd").write_text(_reference_smd(bones), encoding="utf-8")
    solids, masses, joints = ragdoll_template(gmod, opts.gender)
    ex, ey, ez = info["eye_local"]
    h = info["height"]
    jc = []
    for j in joints:
        for axis in ("x", "y", "z"):
            lo, hi, fr = j[f"{axis}min"], j[f"{axis}max"], j[f"{axis}friction"]
            jc.append(f'\t$jointconstrain "{j["bone"]}" {axis} limit {lo:.2f} {hi:.2f} {fr:.2f}')
    qc = f"""$modelname "{model_rel}"
$model "body" "body.smd"
$cdmaterials "{mat_rel}/"
$surfaceprop "flesh"
$contents "solid"
$eyeposition 0 0 {info['eye_height']:.2f}
$illumposition 0 0 {h / 2:.2f}
$cbox 0 0 0 0 0 0
$bbox -13 -13 0 13 13 {max(h, 72):.0f}

$attachment "eyes" "ValveBiped.Bip01_Head1" {ex:.3f} {ey:.3f} {ez:.3f} rotate 0 0 0
$attachment "anim_attachment_RH" "ValveBiped.Anim_Attachment_RH" 0 0 0 rotate 0 0 0
$attachment "anim_attachment_LH" "ValveBiped.Anim_Attachment_LH" 0 0 0 rotate 0 0 0
$attachment "anim_attachment_head" "ValveBiped.Bip01_Head1" 0 0 0 rotate 0 0 0
$attachment "forward" "ValveBiped.forward" 0 0 0 rotate 0 0 0
$bonemerge "ValveBiped.Bip01_R_Hand"
$bonemerge "ValveBiped.Bip01_L_Hand"
$bonemerge "ValveBiped.Bip01_Spine2"
$bonemerge "ValveBiped.Bip01_Head1"

$ikchain "rhand" "ValveBiped.Bip01_R_Hand" knee 0.707 0.707 0
$ikchain "lhand" "ValveBiped.Bip01_L_Hand" knee 0.707 0.707 0
$ikchain "rfoot" "ValveBiped.Bip01_R_Foot" knee 0.707 -0.707 0
$ikchain "lfoot" "ValveBiped.Bip01_L_Foot" knee 0.707 -0.707 0

$includemodel "{g['anims']}"

// «proportion trick»: анимации HL2 подгоняются под пропорции этого персонажа
$sequence "reference" "reference.smd" fps 1
$animation "a_proportions" "body.smd" subtract "reference" 0
$sequence "proportions" "a_proportions" predelta autoplay

$sequence "ragdoll" "body.smd" ACT_DIERAGDOLL 1 fps 30

$collisionjoints "phys.smd"
{{
\t$mass {sum(masses.values()):.1f}
\t$inertia 10.00
\t$damping 0.01
\t$rotdamping 1.50
\t$rootbone "valvebiped.bip01_pelvis"
{chr(10).join(jc)}
}}
"""
    (work / "model.qc").write_text(qc, encoding="utf-8")

    # 3. Компиляция во временную «игру»
    game = work / "game"
    if game.exists():
        shutil.rmtree(game)
    game.mkdir()
    shutil.copy(gmod / "garrysmod" / "gameinfo.txt", game / "gameinfo.txt")
    log("  компиляция studiomdl…")
    out = _run([str(studiomdl), "-game", str(game), "-nop4", "-nox360", str(work / "model.qc")], log,
               cwd=studiomdl.parent)
    compiled = game / "models" / Path(model_rel).parent
    files = sorted(compiled.glob(f"{name}.*"))
    if not any(f.suffix == ".mdl" for f in files):
        raise RuntimeError("studiomdl не создал .mdl:\n" + "\n".join(out.splitlines()[-30:]))
    warnings = [l for l in out.splitlines() if "WARNING" in l.upper()]
    models_out = folder / "models" / Path(model_rel).parent
    models_out.mkdir(parents=True)
    for f in files:
        shutil.copy(f, models_out / f.name)
    log("  скомпилировано: " + ", ".join(f.name for f in files))

    # 4. Регистрация в меню выбора модели (C → Player Model) и руки от первого лица
    lua = folder / "lua" / "autorun"
    lua.mkdir(parents=True)
    title = opts.title.replace('"', "'")
    (lua / f"gm2b_{name}.lua").write_text(
        f'player_manager.AddValidModel("{title}", "models/{model_rel}")\n'
        f'player_manager.AddValidHands("{title}", "{g["hands"]}", 0, "00000000")\n'
        f'list.Set("PlayerOptionsModel", "{title}", "models/{model_rel}")\n', encoding="utf-8")
    (folder / "addon.json").write_text(json.dumps({"title": opts.title, "type": "model",
                                                   "tags": ["fun", "roleplay"], "ignore": []},
                                                  ensure_ascii=False, indent="\t"), encoding="utf-8")
    result = {"folder": folder, "model": f"models/{model_rel}", "gma": None, "installed": None,
              "warnings": warnings}
    if opts.make_gma:
        gma = Path(opts.out_dir) / f"{name}_playermodel.gma"
        _run([str(gmod / "bin" / "gmad.exe"), "create", "-folder", str(folder), "-out", str(gma)], log)
        result["gma"] = gma
    if opts.install:
        target = gmod / "garrysmod" / "addons" / f"gm2b_pm_{name}"
        install_directory(folder, target)
        result["installed"] = target
        log(f"  установлено в игру: {target}")
    log(f"✔ playermodel готов: {result['model']}")
    return result
