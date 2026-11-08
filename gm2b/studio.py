"""Чтение скомпилированных моделей Source: .mdl + .vvd + .vtx.

.mdl — заголовок, кости, bodygroups, список материалов, скины
.vvd — вершины: позиция, нормаль, UV, веса костей
.vtx — треугольники (оптимизированные под железо strip groups)

Поддерживаются версии MDL 44–49 (всё, что встречается в Garry's Mod).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

BONE_SIZE = 216
TEXTURE_SIZE = 64
BODYPART_SIZE = 16
MODEL_SIZE = 148
MESH_SIZE = 116
FLEX_SIZE = 60
VVD_VERTEX_SIZE = 48


class StudioError(Exception):
    pass


def _cstr(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("utf-8", "replace")


# ------------------------------------------------------------------ MDL

@dataclass
class Bone:
    name: str
    parent: int
    pos: tuple[float, float, float]
    quat: tuple[float, float, float, float]  # x, y, z, w


@dataclass
class Flex:
    desc: int        # индекс flexdesc (имя shape key)
    pair: int        # для стерео-flex: второй flexdesc (левая/правая половина), иначе 0
    # (индекс вершины в меше, side 0..255, dx, dy, dz)
    verts: list[tuple[int, int, float, float, float]]


@dataclass
class FlexController:
    name: str
    type: str    # группа: eyes, phoneme, expression, default…
    min: float
    max: float


@dataclass
class FlexRule:
    flex: int                              # индекс flexdesc
    ops: list[tuple[int, int, float]]      # (код операции, int-аргумент, float-аргумент)


@dataclass
class MdlMesh:
    material: int  # индекс в таблице скинов (skinref)
    num_vertices: int
    vertex_offset: int
    flexes: list[Flex] = field(default_factory=list)


@dataclass
class MdlModel:
    name: str
    num_vertices: int
    vertex_index: int  # индекс первой вершины в .vvd
    meshes: list[MdlMesh] = field(default_factory=list)


@dataclass
class BodyPart:
    name: str
    models: list[MdlModel] = field(default_factory=list)


class Mdl:
    def __init__(self, data: bytes):
        ident, self.version, self.checksum = struct.unpack_from("<4sii", data, 0)
        if ident != b"IDST":
            raise StudioError("Это не MDL-файл")
        if not 44 <= self.version <= 49:
            raise StudioError(f"MDL версии {self.version} не поддерживается (нужны 44–49)")

        self.name = _cstr(data[12:76], 0)
        self.flags, num_bones, bone_index = struct.unpack_from("<3i", data, 152)
        (num_tex, tex_index, num_cd, cd_index,
         num_skinref, num_skinfam, skin_index,
         num_bp, bp_index) = struct.unpack_from("<9i", data, 204)

        self.bones: list[Bone] = []
        for i in range(num_bones):
            base = bone_index + i * BONE_SIZE
            name_off, parent = struct.unpack_from("<ii", data, base)
            pos = struct.unpack_from("<3f", data, base + 32)
            quat = struct.unpack_from("<4f", data, base + 44)
            self.bones.append(Bone(_cstr(data, base + name_off), parent, pos, quat))

        self.textures: list[str] = []
        for i in range(num_tex):
            base = tex_index + i * TEXTURE_SIZE
            (name_off,) = struct.unpack_from("<i", data, base)
            self.textures.append(_cstr(data, base + name_off))

        self.cd_textures: list[str] = []
        for i in range(num_cd):
            (off,) = struct.unpack_from("<i", data, cd_index + i * 4)
            self.cd_textures.append(_cstr(data, off))

        # skins[семейство][skinref] -> индекс текстуры
        self.skins: list[list[int]] = [
            list(struct.unpack_from(f"<{num_skinref}h", data, skin_index + fam * num_skinref * 2))
            for fam in range(num_skinfam)
        ] or [list(range(num_tex))]

        (num_flexdesc, flexdesc_index, num_ctrl, ctrl_index,
         num_rules, rule_index) = struct.unpack_from("<6i", data, 260)
        self.flex_names: list[str] = []
        for i in range(num_flexdesc):
            base = flexdesc_index + i * 4
            (name_off,) = struct.unpack_from("<i", data, base)
            self.flex_names.append(_cstr(data, base + name_off))

        # Контроллеры — это слайдеры Face Poser в GMod
        self.flex_controllers: list[FlexController] = []
        for i in range(num_ctrl):
            base = ctrl_index + i * 20
            type_off, name_off, _global, lo, hi = struct.unpack_from("<3i2f", data, base)
            self.flex_controllers.append(
                FlexController(_cstr(data, base + name_off), _cstr(data, base + type_off), lo, hi))

        # Правила: flexdesc = выражение (в обратной польской записи) от контроллеров
        self.flex_rules: list[FlexRule] = []
        for i in range(num_rules):
            base = rule_index + i * 12
            flex, num_ops, op_index = struct.unpack_from("<3i", data, base)
            ops = []
            for k in range(num_ops):
                op, as_int = struct.unpack_from("<ii", data, base + op_index + k * 8)
                (as_float,) = struct.unpack_from("<f", data, base + op_index + k * 8 + 4)
                ops.append((op, as_int, as_float))
            self.flex_rules.append(FlexRule(flex, ops))

        self.bodyparts: list[BodyPart] = []
        for i in range(num_bp):
            base = bp_index + i * BODYPART_SIZE
            name_off, num_models, _base, model_index = struct.unpack_from("<4i", data, base)
            bp = BodyPart(_cstr(data, base + name_off))
            for j in range(num_models):
                mbase = base + model_index + j * MODEL_SIZE
                num_meshes, mesh_index, num_verts, vert_index = struct.unpack_from("<4i", data, mbase + 72)
                model = MdlModel(_cstr(data, mbase), num_verts, vert_index // VVD_VERTEX_SIZE)
                for k in range(num_meshes):
                    mesh_base = mbase + mesh_index + k * MESH_SIZE
                    material, _model, mesh_verts, vert_offset, num_flex, flex_index = \
                        struct.unpack_from("<6i", data, mesh_base)
                    mesh = MdlMesh(material, mesh_verts, vert_offset)
                    for f in range(num_flex):
                        mesh.flexes.append(_read_flex(data, mesh_base + flex_index + f * FLEX_SIZE))
                    model.meshes.append(mesh)
                bp.models.append(model)
            self.bodyparts.append(bp)

    @property
    def is_static_prop(self) -> bool:
        return bool(self.flags & 0x10)

    @property
    def num_flexes(self) -> int:
        """Число shape keys: у стерео-flex отдельный ключ и для правой, и для левой половины."""
        used = set()
        for bp in self.bodyparts:
            for m in bp.models:
                for me in m.meshes:
                    for f in me.flexes:
                        used.add(f.desc)
                        if f.pair:
                            used.add(f.pair)
        return len(used)


# mstudiovertanim_t: index, speed, side, delta[3] (half), ndelta[3] (half);
# у wrinkle-варианта в конце ещё short wrinkledelta
_VERTANIM = struct.Struct("<HBB3e3e")
_VERTANIM_WRINKLE = struct.Struct("<HBB3e3eh")


def _read_flex(data: bytes, base: int) -> Flex:
    desc, = struct.unpack_from("<i", data, base)
    num_verts, vert_index, pair = struct.unpack_from("<3i", data, base + 20)
    anim = _VERTANIM_WRINKLE if data[base + 32] == 1 else _VERTANIM
    start = base + vert_index
    raw = data[start: start + num_verts * anim.size]
    verts = [(v[0], v[2], v[3], v[4], v[5]) for v in anim.iter_unpack(raw)]
    return Flex(desc, pair, verts)


# ------------------------------------------------------------------ VVD

# weight[3], bone[3], numbones, pos[3], normal[3], uv[2]
_VVD_VERTEX = struct.Struct("<3f3BB3f3f2f")


class Vvd:
    """Вершины LOD0 в виде кортежей:
    (w0, w1, w2, b0, b1, b2, numbones, px, py, pz, nx, ny, nz, u, v)."""

    def __init__(self, data: bytes):
        ident, _version, _checksum, _num_lods = struct.unpack_from("<4siii", data, 0)
        if ident != b"IDSV":
            raise StudioError("Это не VVD-файл")
        lod_verts = struct.unpack_from("<8i", data, 16)
        num_fixups, fixup_start, vert_start, tan_start = struct.unpack_from("<4i", data, 48)

        count = (tan_start - vert_start) // VVD_VERTEX_SIZE if tan_start > vert_start else lod_verts[0]
        raw = list(_VVD_VERTEX.iter_unpack(data[vert_start: vert_start + count * VVD_VERTEX_SIZE]))

        if num_fixups:
            # Fixup-таблица собирает вершины нужного LOD из общего массива
            self.vertices = []
            for i in range(num_fixups):
                lod, src, n = struct.unpack_from("<3i", data, fixup_start + i * 12)
                if lod >= 0:
                    self.vertices.extend(raw[src: src + n])
        else:
            self.vertices = raw[: lod_verts[0]]


# ------------------------------------------------------------------ VTX

class Vtx:
    """Треугольники одного LOD: meshes[bodypart][model][mesh] -> [(a, b, c), ...],
    где a/b/c — индексы вершин внутри меша MDL.

    Все LOD ссылаются на один и тот же массив вершин VVD (движок тоже грузит
    вершины один раз), поэтому упрощённая модель — это просто другой LOD в VTX."""

    def __init__(self, data: bytes, mdl: Mdl, lod: int = 0):
        (version,) = struct.unpack_from("<i", data, 0)
        if version != 7:
            raise StudioError(f"VTX версии {version} не поддерживается")
        # Модели, собранные новым studiomdl (CS:GO/SFM), имеют расширенные
        # strip group (+8 байт). Пробуем обычный формат, затем расширенный.
        last_error: Exception | None = None
        for extended in (False, True):
            try:
                self.meshes = self._parse(data, mdl, extended, lod)
                return
            except (struct.error, IndexError, StudioError) as e:
                last_error = e
        raise StudioError(f"Не удалось разобрать VTX: {last_error}")

    @staticmethod
    def _parse(data: bytes, mdl: Mdl, extended: bool, lod: int = 0):
        sg_size = 33 if extended else 25
        strip_size = 35 if extended else 27
        num_bp, bp_off = struct.unpack_from("<ii", data, 28)
        if num_bp != len(mdl.bodyparts):
            raise StudioError("число bodygroups в VTX и MDL не совпадает")

        result = []
        for bpi in range(num_bp):
            bp_base = bp_off + bpi * 8
            num_models, model_off = struct.unpack_from("<ii", data, bp_base)
            bp_out = []
            for mi in range(num_models):
                m_base = bp_base + model_off + mi * 8
                num_lods, lod_off = struct.unpack_from("<ii", data, m_base)
                lod_base = m_base + lod_off + min(lod, max(num_lods - 1, 0)) * 12
                num_meshes, mesh_off = struct.unpack_from("<ii", data, lod_base)
                mdl_meshes = mdl.bodyparts[bpi].models[mi].meshes
                model_out = []
                for mei in range(num_meshes):
                    me_base = lod_base + mesh_off + mei * 9
                    num_sg, sg_off = struct.unpack_from("<ii", data, me_base)
                    limit = mdl_meshes[mei].num_vertices
                    tris = []
                    for sgi in range(num_sg):
                        sg_base = me_base + sg_off + sgi * sg_size
                        nv, voff, ni, ioff, ns, soff = struct.unpack_from("<6i", data, sg_base)
                        orig = [struct.unpack_from("<H", data, sg_base + voff + v * 9 + 4)[0] for v in range(nv)]
                        idx = struct.unpack_from(f"<{ni}H", data, sg_base + ioff)
                        if any(o >= limit for o in orig):
                            raise StudioError("вершина за пределами меша")
                        tris += Vtx._triangles(data, sg_base, soff, ns, strip_size, idx, orig)
                    model_out.append(tris)
                bp_out.append(model_out)
            result.append(bp_out)
        return result

    @staticmethod
    def _triangles(data, sg_base, soff, ns, strip_size, idx, orig):
        out = []
        strips = []
        for s in range(ns):
            st = sg_base + soff + s * strip_size
            s_ni, s_ioff = struct.unpack_from("<ii", data, st)
            strips.append((s_ni, s_ioff, data[st + 18]))
        if not strips:
            strips = [(len(idx), 0, 1)]
        for s_ni, s_ioff, flags in strips:
            chunk = idx[s_ioff: s_ioff + s_ni]
            if flags & 2:  # STRIP_IS_TRISTRIP
                for i in range(len(chunk) - 2):
                    a, b, c = chunk[i], chunk[i + 1], chunk[i + 2]
                    out.append((orig[a], orig[b], orig[c]) if i % 2 == 0 else (orig[b], orig[a], orig[c]))
            else:  # STRIP_IS_TRILIST
                for i in range(0, len(chunk) - 2, 3):
                    out.append((orig[chunk[i]], orig[chunk[i + 1]], orig[chunk[i + 2]]))
        return out


# ------------------------------------------------------------------ всё вместе

@dataclass
class SourceModel:
    path: str
    mdl: Mdl
    vvd: Vvd
    vtx: Vtx

    @classmethod
    def load(cls, vfs, mdl_path: str, lod: int = 0) -> "SourceModel":
        base = mdl_path[:-4]
        mdl = Mdl(vfs.read(mdl_path))
        if not vfs.exists(base + ".vvd"):
            raise StudioError("Нет .vvd — модель неполная")
        vvd = Vvd(vfs.read(base + ".vvd"))
        error = StudioError("Нет .vtx — модель неполная")
        # Варианты .vtx одинаковы по геометрии; если один битый — берём следующий
        for ext in (".dx90.vtx", ".vtx", ".dx80.vtx", ".sw.vtx"):
            if vfs.exists(base + ext):
                try:
                    return cls(mdl_path, mdl, vvd, Vtx(vfs.read(base + ext), mdl, lod))
                except (StudioError, struct.error) as e:
                    error = e
        raise error

    def stats(self) -> dict:
        verts = sum(m.num_vertices for bp in self.mdl.bodyparts for m in bp.models)
        tris = sum(len(t) for bp in self.vtx.meshes for model in bp for t in model)
        return {
            "bones": len(self.mdl.bones),
            "bodygroups": len(self.mdl.bodyparts),
            "vertices": verts,
            "triangles": tris,
            "skins": len(self.mdl.skins),
            "flexes": self.mdl.num_flexes,
            "version": self.mdl.version,
        }
