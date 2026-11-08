"""Материалы Source: .vmt (текстовое описание шейдера) и .vtf (текстуры)."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from PIL import Image

from .sources import norm

# ------------------------------------------------------------------ VTF

DXT1, DXT3, DXT5, DXT1_ONEBITALPHA = 13, 14, 15, 20
_BPP = {0: 4, 1: 4, 2: 3, 3: 3, 4: 2, 5: 1, 6: 2, 7: 1, 8: 1, 9: 3, 10: 3, 11: 4, 12: 4,
        16: 4, 17: 2, 18: 2, 19: 2, 21: 2, 22: 2, 23: 4, 24: 8, 25: 8, 26: 4}
# формат VTF -> (режим PIL, raw-режим распаковки)
_RAW = {0: ("RGBA", "RGBA"), 1: ("RGBA", "ABGR"), 2: ("RGB", "RGB"), 3: ("RGB", "BGR"),
        4: ("RGB", "RGB;16"), 17: ("RGB", "BGR;16"),
        5: ("L", "L"), 6: ("LA", "LA"), 8: ("L", "L"), 9: ("RGB", "RGB"), 10: ("RGB", "BGR"),
        11: ("RGBA", "ARGB"), 12: ("RGBA", "BGRA"), 16: ("RGB", "BGRX")}
_BCN = {DXT1: 1, DXT1_ONEBITALPHA: 1, DXT3: 2, DXT5: 3}


def _image_size(fmt: int, w: int, h: int) -> int:
    if fmt in (DXT1, DXT1_ONEBITALPHA):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
    if fmt in (DXT3, DXT5):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    if fmt not in _BPP:
        raise ValueError(f"неизвестный формат VTF {fmt}")
    return w * h * _BPP[fmt]


def read_vtf(data: bytes) -> Image.Image:
    """Самый крупный mip, первый кадр, первая грань."""
    sig, _vmaj, vmin, header_size = struct.unpack_from("<4s3I", data, 0)
    if sig != b"VTF\0":
        raise ValueError("Это не VTF")
    width, height, flags, frames = struct.unpack_from("<HHIH", data, 16)
    frames = max(frames, 1)
    (hi_fmt,) = struct.unpack_from("<i", data, 52)
    mip_count = data[56]
    (lo_fmt,) = struct.unpack_from("<i", data, 57)
    lo_w, lo_h = data[61], data[62]
    depth = (struct.unpack_from("<H", data, 63)[0] or 1) if vmin >= 2 else 1
    faces = 6 if flags & 0x4000 else 1  # TEXTUREFLAGS_ENVMAP

    if vmin >= 3:
        (num_res,) = struct.unpack_from("<I", data, 68)
        offset = None
        for i in range(num_res):
            tag, _rflags, res_off = struct.unpack_from("<3sBI", data, 80 + i * 8)
            if tag == b"\x30\x00\x00":
                offset = res_off
        if offset is None:
            raise ValueError("в VTF нет основного изображения")
    else:
        lo_size = _image_size(lo_fmt, lo_w, lo_h) if lo_fmt >= 0 and lo_w and lo_h else 0
        offset = header_size + lo_size

    # mip-уровни лежат от мелкого к крупному — пропускаем все, кроме нулевого
    for mip in range(mip_count - 1, 0, -1):
        w, h, d = max(1, width >> mip), max(1, height >> mip), max(1, depth >> mip)
        offset += _image_size(hi_fmt, w, h) * frames * faces * d

    raw = data[offset: offset + _image_size(hi_fmt, width, height)]
    if hi_fmt in _BCN:
        return Image.frombytes("RGBA", (width, height), raw, "bcn", _BCN[hi_fmt])
    if hi_fmt in _RAW:
        mode, rawmode = _RAW[hi_fmt]
        return Image.frombytes(mode, (width, height), raw, "raw", rawmode)
    raise ValueError(f"формат VTF {hi_fmt} пока не поддерживается")


IMAGE_FORMAT_BGR888, IMAGE_FORMAT_BGRA8888 = 3, 12
TEXTUREFLAGS_NORMAL, TEXTUREFLAGS_EIGHTBITALPHA = 0x80, 0x2000


def write_vtf(img: Image.Image, normal: bool = False, max_size: int = 2048) -> bytes:
    """PNG/любая картинка -> VTF 7.2 без сжатия (BGR888 или BGRA8888) с цепочкой mip-уровней.

    Размеры приводятся к степени двойки — так требует движок."""
    alpha = "A" in img.getbands() and img.getextrema()[-1][0] < 255
    img = img.convert("RGBA" if alpha else "RGB")
    w = min(_pow2(img.width), max_size)
    h = min(_pow2(img.height), max_size)
    if img.size != (w, h):
        img = img.resize((w, h), Image.Resampling.LANCZOS)

    mips = [img]
    while mips[-1].width > 1 or mips[-1].height > 1:
        prev = mips[-1]
        mips.append(prev.resize((max(1, prev.width // 2), max(1, prev.height // 2)), Image.Resampling.BOX))

    fmt = IMAGE_FORMAT_BGRA8888 if alpha else IMAGE_FORMAT_BGR888
    flags = (TEXTUREFLAGS_EIGHTBITALPHA if alpha else 0) | (TEXTUREFLAGS_NORMAL if normal else 0)
    header = bytearray(80)
    struct.pack_into("<4s3I", header, 0, b"VTF\0", 7, 2, 80)
    struct.pack_into("<HHIHH", header, 16, w, h, flags, 1, 0)
    struct.pack_into("<3f", header, 32, 0.5, 0.5, 0.5)                 # reflectivity
    struct.pack_into("<f", header, 48, 1.0)                            # bumpmap scale
    struct.pack_into("<iBiBBH", header, 52, fmt, len(mips), -1, 0, 0, 1)  # без low-res превью
    raw = "BGRA" if alpha else "BGR"
    data = b"".join(m.tobytes("raw", raw) for m in reversed(mips))  # от мелкого к крупному
    return bytes(header) + data


def _pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    # ближайшая степень двойки, а не всегда большая: 1100 -> 1024, 1600 -> 2048
    return p if p - n < n - p // 2 else max(1, p // 2)


# ------------------------------------------------------------------ VMT (KeyValues)

_TOKEN = re.compile(r'"([^"]*)"|(\{)|(\})|([^\s{}"]+)')


def parse_keyvalues(text: str) -> tuple[str, dict]:
    text = re.sub(r"//[^\n]*", "", text)
    tokens = []
    for m in _TOKEN.finditer(text):
        if m.group(2):
            tokens.append("{")
        elif m.group(3):
            tokens.append("}")
        else:
            tokens.append(("s", m.group(1) if m.group(1) is not None else m.group(4)))

    pos = 0

    def block() -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            tok = tokens[pos]
            pos += 1
            if tok == "}":
                break
            if tok == "{":
                continue
            key = tok[1].lower()
            if pos < len(tokens) and tokens[pos] == "{":
                pos += 1
                out[key] = block()
            elif pos < len(tokens) and tokens[pos] != "}":
                out[key] = tokens[pos][1]
                pos += 1
        return out

    if not tokens or tokens[0] in ("{", "}"):
        return "", {}
    shader = tokens[0][1].lower()
    pos = 1
    return shader, block()


@dataclass
class MaterialInfo:
    name: str                 # имя из MDL
    vmt_path: str | None      # где нашли .vmt
    shader: str = ""
    base_texture: str | None = None   # путь .vtf
    normal_texture: str | None = None
    base_texture2: str | None = None  # WorldVertexTransition: вторая текстура рельефа
    alpha: str = "OPAQUE"     # OPAQUE | CLIP | BLEND
    nocull: bool = False
    water: bool = False
    color: tuple[float, float, float] | None = None  # сплошной цвет без текстуры (toolsblack)

    @property
    def found(self) -> bool:
        return self.vmt_path is not None and self.base_texture is not None


def _truthy(v) -> bool:
    return str(v).strip() not in ("", "0")


def load_vmt(vfs, path: str, depth: int = 0) -> tuple[str, dict]:
    shader, params = parse_keyvalues(vfs.read(path).decode("utf-8", "replace"))
    # patch-материал: берёт другой .vmt и дописывает/заменяет параметры
    if shader == "patch" and "include" in params and depth < 4:
        inc = norm(params["include"])
        if vfs.exists(inc):
            shader, base = load_vmt(vfs, inc, depth + 1)
            base.update(params.get("insert", {}))
            base.update(params.get("replace", {}))
            params = base
    return shader, params


def _texture_path(vfs, name: str | None) -> str | None:
    if not name or not isinstance(name, str):
        return None
    p = norm(f"materials/{name}")
    if not p.endswith(".vtf"):
        p += ".vtf"
    return p if vfs.exists(p) else None


def resolve_material(vfs, mdl, tex_index: int) -> MaterialInfo:
    name = norm(mdl.textures[tex_index])
    vmt = None
    for cd in mdl.cd_textures + [""]:
        candidate = norm(f"materials/{cd}/{name}.vmt")
        if vfs.exists(candidate):
            vmt = candidate
            break
    return material_from_vmt(vfs, vmt, name)


def material_from_vmt(vfs, vmt: str | None, name: str) -> MaterialInfo:
    """Материал по пути к .vmt (для брашей карты путь известен сразу)."""
    info = MaterialInfo(name=name, vmt_path=vmt)
    if vmt is None:
        return info
    try:
        info.shader, p = load_vmt(vfs, vmt)
    except (KeyError, FileNotFoundError, UnicodeError):
        info.vmt_path = None
        return info
    info.base_texture = _texture_path(vfs, p.get("$basetexture") or p.get("$iris"))
    info.base_texture2 = _texture_path(vfs, p.get("$basetexture2"))
    info.normal_texture = _texture_path(vfs, p.get("$bumpmap") or p.get("$normalmap"))
    info.water = info.shader == "water"
    if _truthy(p.get("$translucent", 0)) or info.water:
        info.alpha = "BLEND"
    elif _truthy(p.get("$alphatest", 0)):
        info.alpha = "CLIP"
    info.nocull = _truthy(p.get("$nocull", 0))
    return info


def resolve_world_material(vfs, texture_name: str) -> MaterialInfo:
    name = norm(texture_name)
    vmt = norm(f"materials/{name}.vmt")
    info = material_from_vmt(vfs, vmt if vfs.exists(vmt) else None, name)
    if name.startswith("tools/toolsblack"):
        info.color = (0.0, 0.0, 0.0)
    elif name.startswith("tools/toolswhite"):
        info.color = (1.0, 1.0, 1.0)
    return info
