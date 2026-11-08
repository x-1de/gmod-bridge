"""Чтение карт Source (.bsp, VBSP версии 19–21).

Берём из карты:
  • геометрию брашей мира и брашевых энтити (func_door, func_brush…)
  • displacement — рельеф (холмы, неровные поверхности) с весами смешивания текстур
  • статические пропы (game lump "sprp") и prop_* из энтити
  • источники света
  • pakfile — zip внутри BSP с собственными материалами карты
"""
from __future__ import annotations

import lzma
import re
import struct
from dataclasses import dataclass, field

import numpy as np

ENTITIES, PLANES, TEXDATA, VERTEXES, NODES, TEXINFO, FACES = 0, 1, 2, 3, 5, 6, 7
LEAFS, EDGES, SURFEDGES, MODELS, LEAFFACES, DISPINFO, DISP_VERTS = 10, 12, 13, 14, 16, 26, 33
GAME_LUMP, PAKFILE, TEXDATA_STRING_DATA, TEXDATA_STRING_TABLE, FACES_HDR = 35, 40, 43, 44, 58

SURF_SKY2D, SURF_SKY, SURF_TRIGGER, SURF_NODRAW, SURF_HINT, SURF_SKIP = 0x2, 0x4, 0x40, 0x80, 0x100, 0x200
SURF_HIDDEN = SURF_SKY2D | SURF_SKY | SURF_TRIGGER | SURF_NODRAW | SURF_HINT | SURF_SKIP

# Tool-текстуры, которые в игре не видны. Остальные tools/* (toolsblack, toolsblack_forreal,
# toolswhite…) — обычные видимые поверхности, их скрывать нельзя, иначе в карте дыры.
INVISIBLE_TOOLS = {
    "tools/toolsnodraw", "tools/toolsclip", "tools/toolsplayerclip", "tools/toolsnpcclip",
    "tools/toolsinvisible", "tools/toolsinvisibleladder", "tools/toolsareaportal",
    "tools/toolsoccluder", "tools/toolstrigger", "tools/toolsskybox", "tools/toolsskybox2d",
    "tools/toolsskip", "tools/toolshint", "tools/toolsorigin", "tools/toolsfog",
    "tools/toolsblocklight", "tools/toolsblock_los", "tools/toolsblockbullets",
    "tools/toolsblockbullets2", "tools/toolsgrenadeclip", "tools/toolsdotted",
    "tools/toolshide", "tools/toolsinvisibledisplacement", "tools/toolsstairs",
}

FACE_DTYPE = np.dtype([
    ("planenum", "<u2"), ("side", "u1"), ("onnode", "u1"), ("firstedge", "<i4"), ("numedges", "<i2"),
    ("texinfo", "<i2"), ("dispinfo", "<i2"), ("fogvolume", "<i2"), ("styles", "u1", 4), ("lightofs", "<i4"),
    ("area", "<f4"), ("lm_mins", "<i4", 2), ("lm_size", "<i4", 2), ("origface", "<i4"),
    ("numprims", "<u2"), ("firstprim", "<u2"), ("smoothing", "<u4"),
])
TEXINFO_DTYPE = np.dtype([("tex", "<f4", (2, 4)), ("lm", "<f4", (2, 4)), ("flags", "<i4"), ("texdata", "<i4")])
TEXDATA_DTYPE = np.dtype([("reflect", "<f4", 3), ("name", "<i4"), ("w", "<i4"), ("h", "<i4"),
                          ("vw", "<i4"), ("vh", "<i4")])
MODEL_DTYPE = np.dtype([("mins", "<f4", 3), ("maxs", "<f4", 3), ("origin", "<f4", 3),
                        ("headnode", "<i4"), ("firstface", "<i4"), ("numfaces", "<i4")])
DISPVERT_DTYPE = np.dtype([("vec", "<f4", 3), ("dist", "<f4"), ("alpha", "<f4")])


class BspError(Exception):
    pass


@dataclass
class MeshArrays:
    """Геометрия в «плоском» виде для быстрой передачи в Blender."""
    positions: np.ndarray        # (V, 3) float32
    loop_verts: np.ndarray       # (L,) int32
    face_starts: np.ndarray      # (F,) int32
    face_sizes: np.ndarray       # (F,) int32
    uvs: np.ndarray              # (L, 2) float32
    face_materials: np.ndarray   # (F,) int32 — индекс в Bsp.texture_names
    face_smooth: np.ndarray      # (F,) bool — displacement гладкие, браши плоские
    blend: np.ndarray            # (V,) float32 — смешивание двух текстур (displacement)

    @property
    def empty(self) -> bool:
        return len(self.face_starts) == 0


@dataclass
class PropPlacement:
    model: str
    origin: tuple[float, float, float]
    angles: tuple[float, float, float]  # pitch, yaw, roll (градусы)
    skin: int = 0
    scale: float = 1.0


@dataclass
class Light:
    kind: str                       # point | spot | sun
    origin: tuple[float, float, float]
    color: tuple[float, float, float]
    brightness: float
    direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
    cone: float = 45.0
    inner_cone: float = 30.0


@dataclass
class BrushEntity:
    classname: str
    model: int
    origin: tuple[float, float, float]
    angles: tuple[float, float, float]


class Bsp:
    def __init__(self, data: bytes):
        ident, self.version = struct.unpack_from("<4si", data, 0)
        if ident != b"VBSP":
            raise BspError("Это не карта Source (VBSP)")
        if not 19 <= self.version <= 21:
            raise BspError(f"BSP версии {self.version} не поддерживается")
        self.data = data
        self.lumps = [struct.unpack_from("<iii4s", data, 8 + i * 16) for i in range(64)]

        self.planes = np.frombuffer(self.lump(PLANES), "<f4").reshape(-1, 5)[:, :4]
        self.vertices = np.frombuffer(self.lump(VERTEXES), "<f4").reshape(-1, 3)
        self.edges = np.frombuffer(self.lump(EDGES), "<u2").reshape(-1, 2)
        self.surfedges = np.frombuffer(self.lump(SURFEDGES), "<i4")
        faces = self.lump(FACES) or self.lump(FACES_HDR)
        self.faces = np.frombuffer(faces, FACE_DTYPE)
        self.texinfo = np.frombuffer(self.lump(TEXINFO), TEXINFO_DTYPE)
        self.texdata = np.frombuffer(self.lump(TEXDATA), TEXDATA_DTYPE)
        self.models = np.frombuffer(self.lump(MODELS), MODEL_DTYPE)
        self.dispinfo = self.lump(DISPINFO)
        self.dispverts = np.frombuffer(self.lump(DISP_VERTS), DISPVERT_DTYPE)

        strings = self.lump(TEXDATA_STRING_DATA)
        table = np.frombuffer(self.lump(TEXDATA_STRING_TABLE), "<i4")
        self.texture_names = []
        for td in self.texdata:
            off = int(table[td["name"]])
            end = strings.find(b"\0", off)
            self.texture_names.append(strings[off:end].decode("utf-8", "replace").replace("\\", "/").lower())

        self.entities = parse_entities(self.lump(ENTITIES).decode("utf-8", "replace"))

        # Дерево BSP: нужно, чтобы отделить 3D-скайбокс (отдельная «зона» карты)
        self.nodes = np.frombuffer(self.lump(NODES), "<i4").reshape(-1, 8)[:, :3]  # plane, child0, child1
        leafs = self.lump(LEAFS)
        leaf_size = 56 if self.lumps[LEAFS][2] == 0 and len(leafs) % 56 == 0 else 32
        self.leaf_area = np.frombuffer(leafs, "<u2").reshape(-1, leaf_size // 2)[:, 3] & 0x1FF
        leaf_faces = np.frombuffer(leafs, "<u2").reshape(-1, leaf_size // 2)[:, 10:12]
        self.leaf_face_ranges = leaf_faces.astype(np.int64)
        self.leaffaces = np.frombuffer(self.lump(LEAFFACES), "<u2")

    # ------------------------------------------------------------ 3D-скайбокс

    def point_leaf(self, p) -> int:
        node = int(self.models[0]["headnode"])
        p = np.asarray(p, np.float64)
        while node >= 0:
            plane, front, back = self.nodes[node]
            n = self.planes[plane]
            node = int(front if p @ n[:3] - n[3] >= 0 else back)
        return -1 - node

    def skybox_area(self) -> int | None:
        cam = next((e for e in self.entities if e.get("classname") == "sky_camera"), None)
        if cam is None:
            return None
        return int(self.leaf_area[self.point_leaf(_vec(cam.get("origin")))])

    def skybox_faces(self) -> set[int]:
        """Грани мира, лежащие в зоне 3D-скайбокса."""
        area = self.skybox_area()
        if area is None:
            return set()
        out = set()
        for leaf in np.nonzero(self.leaf_area == area)[0]:
            first, count = self.leaf_face_ranges[leaf]
            out.update(int(f) for f in self.leaffaces[first: first + count])
        return out

    def in_area(self, p, area: int | None) -> bool:
        return area is not None and int(self.leaf_area[self.point_leaf(p)]) == area

    # ------------------------------------------------------------ лампы

    def lump(self, index: int) -> bytes:
        ofs, length, _ver, _cc = self.lumps[index]
        return _maybe_lzma(self.data[ofs: ofs + length])

    @property
    def pakfile(self) -> bytes:
        return self.lump(PAKFILE)

    # ------------------------------------------------------------ геометрия

    def _hidden(self, ti: int) -> bool:
        if ti < 0:
            return True
        info = self.texinfo[ti]
        if info["flags"] & SURF_HIDDEN:
            return True
        return self.texture_names[info["texdata"]] in INVISIBLE_TOOLS

    def model_mesh(self, model_index: int, only: set[int] | None = None,
                   exclude: set[int] | None = None) -> MeshArrays:
        m = self.models[model_index]
        b = _MeshBuilder()
        for fi in range(m["firstface"], m["firstface"] + m["numfaces"]):
            if (only is not None and fi not in only) or (exclude and fi in exclude):
                continue
            face = self.faces[fi]
            ti = int(face["texinfo"])
            if self._hidden(ti):
                continue
            ids = self._face_vertex_ids(face)
            if len(ids) < 3:
                continue
            if face["dispinfo"] >= 0:
                self._add_displacement(b, face, ids, ti)
            else:
                self._add_polygon(b, face, ids, ti)
        return b.build()

    def _face_vertex_ids(self, face) -> np.ndarray:
        se = self.surfedges[face["firstedge"]: face["firstedge"] + face["numedges"]]
        e = self.edges[np.abs(se)]
        return np.where(se >= 0, e[:, 0], e[:, 1]).astype(np.int64)

    def _plane_normal(self, face) -> np.ndarray:
        # Нормаль плоскости грани уже смотрит в лицевую сторону. Поле "side" относится
        # к узлу дерева BSP и для ориентации не нужно: если учитывать его, ~40% граней
        # разворачиваются внутрь стен, и Blender их отсекает — в карте появляются дыры.
        return self.planes[face["planenum"], :3]

    def _uv(self, ti: int, pos: np.ndarray) -> np.ndarray:
        info = self.texinfo[ti]
        td = self.texdata[info["texdata"]]
        s, t = info["tex"][0], info["tex"][1]
        u = (pos @ s[:3] + s[3]) / max(int(td["w"]), 1)
        v = (pos @ t[:3] + t[3]) / max(int(td["h"]), 1)
        return np.stack([u, 1.0 - v], axis=-1)

    def _add_polygon(self, b, face, ids, ti):
        pos = self.vertices[ids]
        # Порядок вершин приводим к нормали плоскости (Blender: против часовой)
        if _newell(pos) @ self._plane_normal(face) < 0:
            ids, pos = ids[::-1], pos[::-1]
        vi = b.add_vertices(pos)
        b.add_face(vi, self._uv(ti, pos), int(self.texinfo[ti]["texdata"]), smooth=False)

    def _add_displacement(self, b, face, ids, ti):
        if len(ids) != 4:
            return
        base = 176 * int(face["dispinfo"])
        start = np.array(struct.unpack_from("<3f", self.dispinfo, base))
        vert_start, _tri_start, power = struct.unpack_from("<3i", self.dispinfo, base + 12)
        corners = self.vertices[ids]
        first = int(np.argmin(np.linalg.norm(corners - start, axis=1)))
        corners = np.roll(corners, -first, axis=0)

        n = (1 << power) + 1
        dv = self.dispverts[vert_start: vert_start + n * n]
        fy = np.linspace(0.0, 1.0, n)[:, None, None]
        fx = np.linspace(0.0, 1.0, n)[None, :, None]
        left = corners[0] + (corners[1] - corners[0]) * fy
        right = corners[3] + (corners[2] - corners[3]) * fy
        flat = (left + (right - left) * fx).reshape(-1, 3)
        pos = (flat + dv["vec"] * dv["dist"][:, None]).astype(np.float32)
        uv = self._uv(ti, flat)
        vi = b.add_vertices(pos, blend=dv["alpha"] / 255.0)

        # сетка из квадов; ориентацию сверяем с нормалью исходной грани
        quad = np.array([0, n, n + 1, 1])
        if _newell(flat[quad]) @ self._plane_normal(face) < 0:
            quad = quad[::-1]
        y, x = np.mgrid[0:n - 1, 0:n - 1]
        q = ((y * n + x).reshape(-1, 1) + quad).astype(np.int64)  # (K, 4)
        b.add_quads(vi[q], uv[q], int(self.texinfo[ti]["texdata"]))

    # ------------------------------------------------------------ объекты

    def brush_entities(self) -> list[BrushEntity]:
        out = []
        for e in self.entities:
            m = e.get("model", "")
            if m.startswith("*") and m[1:].isdigit() and int(m[1:]) < len(self.models):
                out.append(BrushEntity(e.get("classname", ""), int(m[1:]),
                                       _vec(e.get("origin")), _vec(e.get("angles"))))
        return out

    def static_props(self) -> list[PropPlacement]:
        lump = self.lump(GAME_LUMP)
        if len(lump) < 4:
            return []
        (count,) = struct.unpack_from("<i", lump, 0)
        for i in range(count):
            gid, flags, version, ofs, length = struct.unpack_from("<4sHHii", lump, 4 + i * 16)
            if gid[::-1] == b"sprp":
                data = _maybe_lzma(self.data[ofs: ofs + length])
                return _parse_sprp(data, version)
        return []

    def entity_props(self) -> list[PropPlacement]:
        out = []
        for e in self.entities:
            cls = e.get("classname", "")
            model = e.get("model", "")
            if cls.startswith(("prop_", "monster_generic")) and model.lower().endswith(".mdl"):
                out.append(PropPlacement(
                    model.replace("\\", "/").lower(), _vec(e.get("origin")), _vec(e.get("angles")),
                    int(float(e.get("skin", 0) or 0)), float(e.get("modelscale", 1) or 1)))
        return out

    def lights(self) -> list[Light]:
        out = []
        for e in self.entities:
            cls = e.get("classname", "")
            if cls not in ("light", "light_spot", "light_environment"):
                continue
            parts = (e.get("_light") or "255 255 255 200").split()
            vals = [float(p) for p in parts] + [255.0, 255.0, 255.0, 200.0][len(parts):]
            color = tuple(min(max(c / 255.0, 0.0), 1.0) for c in vals[:3])
            angles = _vec(e.get("angles"))
            # Ключ "pitch" у света: -90 = вниз; в "angles" наоборот, +90 = вниз
            pitch = -float(e["pitch"]) if "pitch" in e else angles[0]
            direction = _forward((pitch, angles[1], 0.0))
            kind = {"light": "point", "light_spot": "spot", "light_environment": "sun"}[cls]
            out.append(Light(kind, _vec(e.get("origin")), color, vals[3], direction,
                             float(e.get("_cone", 45) or 45), float(e.get("_inner_cone", 30) or 30)))
        return out


# ------------------------------------------------------------------ вспомогательное

class _MeshBuilder:
    def __init__(self):
        self.pos, self.blend = [], []
        self.loops, self.uvs, self.sizes, self.mats, self.smooth = [], [], [], [], []
        self.nv = 0

    def add_vertices(self, pos, blend=None) -> np.ndarray:
        self.pos.append(np.asarray(pos, np.float32))
        self.blend.append(np.zeros(len(pos), np.float32) if blend is None else np.asarray(blend, np.float32))
        ids = np.arange(self.nv, self.nv + len(pos))
        self.nv += len(pos)
        return ids

    def add_face(self, vids, uvs, mat, smooth):
        self.loops.append(np.asarray(vids, np.int32))
        self.uvs.append(np.asarray(uvs, np.float32))
        self.sizes.append(len(vids))
        self.mats.append(mat)
        self.smooth.append(smooth)

    def add_quads(self, vids, uvs, mat):
        """Пачка квадов (K, 4) — для displacement, гладкие."""
        k = len(vids)
        self.loops.append(np.asarray(vids, np.int32).ravel())
        self.uvs.append(np.asarray(uvs, np.float32).reshape(-1, 2))
        self.sizes.extend([4] * k)
        self.mats.extend([mat] * k)
        self.smooth.extend([True] * k)

    def build(self) -> MeshArrays:
        if not self.sizes:
            z = np.zeros(0, np.int32)
            return MeshArrays(np.zeros((0, 3), np.float32), z, z, z, np.zeros((0, 2), np.float32),
                              z, np.zeros(0, bool), np.zeros(0, np.float32))
        sizes = np.array(self.sizes, np.int32)
        starts = np.concatenate([[0], np.cumsum(sizes)[:-1]]).astype(np.int32)
        return MeshArrays(np.concatenate(self.pos), np.concatenate(self.loops), starts, sizes,
                          np.concatenate(self.uvs), np.array(self.mats, np.int32),
                          np.array(self.smooth, bool), np.concatenate(self.blend))


def _newell(pos: np.ndarray) -> np.ndarray:
    nxt = np.roll(pos, -1, axis=0)
    return np.array([
        np.sum((pos[:, 1] - nxt[:, 1]) * (pos[:, 2] + nxt[:, 2])),
        np.sum((pos[:, 2] - nxt[:, 2]) * (pos[:, 0] + nxt[:, 0])),
        np.sum((pos[:, 0] - nxt[:, 0]) * (pos[:, 1] + nxt[:, 1])),
    ])


def _maybe_lzma(raw: bytes) -> bytes:
    """Лампы некоторых карт сжаты: 'LZMA', actualSize, lzmaSize, props[5], данные."""
    if raw[:4] != b"LZMA":
        return raw
    actual, packed = struct.unpack_from("<II", raw, 4)
    header = raw[12:17] + struct.pack("<Q", actual)
    return lzma.decompress(header + raw[17:17 + packed], format=lzma.FORMAT_ALONE)[:actual]


def _vec(s) -> tuple[float, float, float]:
    try:
        v = [float(x) for x in str(s or "0 0 0").split()[:3]]
    except ValueError:
        v = [0.0, 0.0, 0.0]
    return tuple(v + [0.0] * (3 - len(v)))


def _forward(angles) -> tuple[float, float, float]:
    """Вектор «вперёд» для QAngle Source (pitch вниз — положительный)."""
    p, y = np.radians(angles[0]), np.radians(angles[1])
    return (float(np.cos(p) * np.cos(y)), float(np.cos(p) * np.sin(y)), float(-np.sin(p)))


_ENT_BLOCK = re.compile(r"\{([^{}]*)\}")
_ENT_PAIR = re.compile(r'"([^"]*)"\s*"([^"]*)"')


def parse_entities(text: str) -> list[dict]:
    out = []
    for block in _ENT_BLOCK.finditer(text):
        ent = {}
        for k, v in _ENT_PAIR.findall(block.group(1)):
            ent.setdefault(k.lower(), v)
        out.append(ent)
    return out


def _parse_sprp(data: bytes, version: int) -> list[PropPlacement]:
    (n_names,) = struct.unpack_from("<i", data, 0)
    names = []
    for i in range(n_names):
        raw = data[4 + i * 128: 4 + (i + 1) * 128]
        names.append(raw.split(b"\0")[0].decode("utf-8", "replace").replace("\\", "/").lower())
    pos = 4 + n_names * 128
    (n_leaf,) = struct.unpack_from("<i", data, pos)
    pos += 4 + n_leaf * 2
    (n_props,) = struct.unpack_from("<i", data, pos)
    pos += 4
    if n_props == 0:
        return []
    size = (len(data) - pos) // n_props  # размер записи зависит от версии лампа
    out = []
    for i in range(n_props):
        base = pos + i * size
        origin = struct.unpack_from("<3f", data, base)
        angles = struct.unpack_from("<3f", data, base + 12)
        (ptype,) = struct.unpack_from("<H", data, base + 24)
        (skin,) = struct.unpack_from("<i", data, base + 32)
        scale = 1.0
        if version >= 11 and size >= 76:
            (scale,) = struct.unpack_from("<f", data, base + size - 4)
            if not 0.01 < scale < 100:
                scale = 1.0
        if ptype < len(names):
            out.append(PropPlacement(names[ptype], origin, angles, skin, scale))
    return out
