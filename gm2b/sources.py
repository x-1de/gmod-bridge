"""Источники файлов: архивы .gma, распакованные папки, VPK игры.

Все пути внутри приводятся к виду "materials/models/foo/bar.vmt"
(нижний регистр, прямые слэши), как это делает сам движок Source.
"""
from __future__ import annotations

import io
import json
import lzma
import os
import re
import struct
import threading
from pathlib import Path


def norm(path: str) -> str:
    p = path.replace("\\", "/").lower()
    while "//" in p:
        p = p.replace("//", "/")
    return p.strip("/")


def _read_cstring(f) -> str:
    buf = bytearray()
    while True:
        c = f.read(1)
        if not c or c == b"\0":
            break
        buf += c
    return buf.decode("utf-8", "replace")


class Source:
    """Базовый интерфейс источника файлов."""

    title = ""
    subtitle = ""

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def read(self, path: str) -> bytes:
        raise NotImplementedError

    def files(self) -> list[str]:
        return []

    def models(self) -> list[str]:
        return sorted(p for p in self.files() if p.startswith("models/") and p.endswith(".mdl"))

    def maps(self) -> list[str]:
        return sorted(p for p in self.files() if p.startswith("maps/") and p.endswith(".bsp"))


class GmaSource(Source):
    """Аддон Garry's Mod (.gma, либо старый LZMA-сжатый .bin из Workshop)."""

    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        f = open(self.path, "rb")
        if f.read(4) != b"GMAD":
            f.close()
            f = self._decompress_legacy(self.path)
            if f.read(4) != b"GMAD":
                raise ValueError("Это не архив GMA")
        self._f = f
        self._lock = threading.Lock()

        version = f.read(1)[0]
        f.read(16)  # steamid64 + timestamp
        if version > 1:
            while _read_cstring(f):  # список required content, обычно пустой
                pass
        self.title = _read_cstring(f) or Path(self.path).stem
        description = _read_cstring(f)
        self.author = _read_cstring(f)
        f.read(4)  # addon version

        raw = []
        while True:
            (num,) = struct.unpack("<I", f.read(4))
            if num == 0:
                break
            name = _read_cstring(f)
            size, _crc = struct.unpack("<qI", f.read(12))
            raw.append((name, size))

        offset = f.tell()
        self._entries: dict[str, tuple[int, int]] = {}
        for name, size in raw:
            self._entries[norm(name)] = (offset, size)
            offset += size

        # В новых аддонах описание — JSON с полями description/type/tags
        self.description = description
        try:
            meta = json.loads(description)
            if isinstance(meta, dict):
                self.description = meta.get("description", "")
                self.addon_type = meta.get("type", "")
        except ValueError:
            pass
        self.subtitle = Path(self.path).name

    @staticmethod
    def _decompress_legacy(path: str):
        data = Path(path).read_bytes()
        for fmt in (lzma.FORMAT_ALONE, lzma.FORMAT_AUTO):
            try:
                return io.BytesIO(lzma.decompress(data, format=fmt))
            except lzma.LZMAError:
                continue
        raise ValueError("Не удалось распаковать .bin (ожидался LZMA)")

    def exists(self, path: str) -> bool:
        return norm(path) in self._entries

    def read(self, path: str) -> bytes:
        offset, size = self._entries[norm(path)]
        with self._lock:
            self._f.seek(offset)
            return self._f.read(size)

    def files(self) -> list[str]:
        return list(self._entries)


class FolderSource(Source):
    """Распакованный аддон или папка garrysmod/ (models/, materials/ внутри)."""

    def __init__(self, root: str | os.PathLike, title: str | None = None, index: bool = True):
        self.root = Path(root)
        self.title = title or self.root.name
        self.subtitle = str(self.root)
        self._index: list[str] | None = None
        if index:
            self._index = [
                norm(str(p.relative_to(self.root)))
                for p in self.root.rglob("*")
                if p.is_file()
            ]

    def exists(self, path: str) -> bool:
        return self._safe_path(path).is_file()

    def read(self, path: str) -> bytes:
        return self._safe_path(path).read_bytes()

    def _safe_path(self, path: str) -> Path:
        root = self.root.resolve()
        target = (root / norm(path)).resolve()
        if target != root and root not in target.parents:
            raise ValueError("Путь выходит за пределы папки аддона")
        return target

    def files(self) -> list[str]:
        return self._index or []


class VpkSource(Source):
    """VPK-архив игры (*_dir.vpk). Нужен, чтобы находить текстуры HL2/GMod,
    на которые ссылаются модели из аддонов."""

    def __init__(self, dir_path: str | os.PathLike):
        self.dir_path = Path(dir_path)
        self.title = self.dir_path.name
        self.subtitle = str(self.dir_path)
        self._prefix = str(self.dir_path)[: -len("_dir.vpk")]
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[int, int, int, bytes]] = {}

        with open(self.dir_path, "rb") as f:
            sig, version, tree_size = struct.unpack("<III", f.read(12))
            if sig != 0x55AA1234:
                raise ValueError(f"{self.dir_path.name}: не VPK")
            header_size = 12 if version == 1 else 28
            f.seek(header_size)
            tree = io.BytesIO(f.read(tree_size))
        self._data_start = header_size + tree_size

        while ext := _read_cstring(tree):
            while folder := _read_cstring(tree):
                while name := _read_cstring(tree):
                    _crc, preload, archive, offset, length, _term = struct.unpack("<IHHIIH", tree.read(18))
                    preload_data = tree.read(preload) if preload else b""
                    full = name if ext == " " else f"{name}.{ext}"
                    if folder != " ":
                        full = f"{folder}/{full}"
                    self._entries[norm(full)] = (archive, offset, length, preload_data)

    def exists(self, path: str) -> bool:
        return norm(path) in self._entries

    def read(self, path: str) -> bytes:
        archive, offset, length, preload = self._entries[norm(path)]
        if length == 0:
            return preload
        if archive == 0x7FFF:
            file, offset = self.dir_path, offset + self._data_start
        else:
            file = f"{self._prefix}_{archive:03d}.vpk"
        with self._lock, open(file, "rb") as f:
            f.seek(offset)
            return preload + f.read(length)


class ZipSource(Source):
    """Pakfile карты: zip-архив внутри BSP с текстурами/моделями, вшитыми в карту."""

    def __init__(self, data: bytes, title: str = "pakfile"):
        import zipfile

        self.title = title
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self._names = {norm(n): n for n in self._zip.namelist() if not n.endswith("/")}
        self._lock = threading.Lock()

    def exists(self, path: str) -> bool:
        return norm(path) in self._names

    def read(self, path: str) -> bytes:
        with self._lock:
            return self._zip.read(self._names[norm(path)])

    def files(self) -> list[str]:
        return list(self._names)


class VirtualFS:
    """Набор источников; файл ищется по порядку — первый найденный побеждает."""

    def __init__(self, sources: list[Source]):
        self.sources = sources

    def find(self, path: str) -> Source | None:
        for s in self.sources:
            if s.exists(path):
                return s
        return None

    def exists(self, path: str) -> bool:
        return self.find(path) is not None

    def read(self, path: str) -> bytes:
        s = self.find(path)
        if s is None:
            raise FileNotFoundError(path)
        return s.read(path)


# ---------------------------------------------------------------- поиск игры

def steam_libraries() -> list[Path]:
    roots: list[Path] = []
    steam = None
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        except OSError:
            pass
    else:
        steam = Path.home() / ".steam" / "steam"
    if steam and steam.exists():
        roots.append(steam)
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text("utf-8", "replace")):
                p = Path(m.group(1).replace("\\\\", "\\"))
                if p not in roots:
                    roots.append(p)
    return roots


def find_gmod_dir() -> Path | None:
    """Папка .../steamapps/common/GarrysMod."""
    for lib in steam_libraries():
        p = lib / "steamapps" / "common" / "GarrysMod"
        if (p / "garrysmod").is_dir():
            return p
    return None


def find_workshop_addons(gmod_dir: Path | None) -> list[Path]:
    """Все скачанные аддоны: подписки Workshop + кэш игры + папка addons."""
    found: list[Path] = []
    for lib in steam_libraries():
        ws = lib / "steamapps" / "workshop" / "content" / "4000"
        if ws.is_dir():
            found += [p for p in ws.rglob("*") if p.suffix.lower() in (".gma", ".bin")]
    if gmod_dir:
        for sub in ("garrysmod/cache/workshop", "garrysmod/addons"):
            d = gmod_dir / sub
            if d.is_dir():
                found += [p for p in d.glob("*.gma")]
    return found


def game_sources(gmod_dir: Path | None) -> list[Source]:
    """Контент самой игры: loose-файлы и VPK с текстурами HL2/GMod."""
    if not gmod_dir:
        return []
    out: list[Source] = [FolderSource(gmod_dir / "garrysmod", "garrysmod (файлы)", index=False)]
    for rel in (
        "garrysmod/garrysmod_dir.vpk",
        "sourceengine/hl2_textures_dir.vpk",
        "sourceengine/hl2_misc_dir.vpk",
        "sourceengine/content_hl2_dir.vpk",
        "sourceengine/content_cstrike_dir.vpk",
    ):
        p = gmod_dir / rel
        if p.exists():
            try:
                out.append(VpkSource(p))
            except (OSError, ValueError, struct.error):
                pass
    return out
