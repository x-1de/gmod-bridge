import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from gm2b.materials import read_vtf, write_vtf, parse_keyvalues
from gm2b.sources import FolderSource, VirtualFS, GmaSource
from gm2b.storage import install_directory
from gm2b.exporter import ExportOptions
from gm2b.flexrules import compile_flex, evaluate, FETCH1, CONST, MUL, ADD


class CoreTests(unittest.TestCase):
    def test_vtf_roundtrip_preserves_alpha_and_pixels(self):
        original = Image.new("RGBA", (16, 8), (12, 73, 201, 89))
        result = read_vtf(write_vtf(original))
        self.assertEqual(result.size, original.size)
        self.assertEqual(result.tobytes(), original.tobytes())

    def test_vmt_nested_patch(self):
        shader, params = parse_keyvalues('patch { "include" "base.vmt" replace { "$alpha" "0.5" } }')
        self.assertEqual(shader, "patch")
        self.assertEqual(params["replace"]["$alpha"], "0.5")

    def test_folder_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "addon"
            root.mkdir()
            (Path(d) / "private.txt").write_text("private")
            src = FolderSource(root)
            with self.assertRaises(ValueError):
                src.read("../private.txt")

    def test_virtual_fs_priority_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            a.mkdir(); b.mkdir()
            (a / "test").write_bytes(b"first")
            (b / "test").write_bytes(b"second")
            fs = VirtualFS([FolderSource(a), FolderSource(b)])
            self.assertEqual(fs.read("test"), b"first")
            with self.assertRaises(FileNotFoundError):
                fs.read("absent")

    def test_gma_fixture(self):
        payload = b"fixture"
        data = b"GMAD\x03" + bytes(16) + b"\0Title\0{}\0Author\0" + bytes(4)
        data += struct.pack("<I", 1) + b"models/test.mdl\0" + struct.pack("<qI", len(payload), 0)
        data += bytes(4) + payload
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.gma"
            p.write_bytes(data)
            src = GmaSource(p)
            try:
                self.assertEqual(src.title, "Title")
                self.assertEqual(src.read("models/test.mdl"), payload)
            finally:
                src._f.close()

    def test_failed_install_preserves_previous_addon(self):
        with tempfile.TemporaryDirectory() as d:
            src, target = Path(d) / "src", Path(d) / "installed"
            src.mkdir(); target.mkdir()
            (target / "old").write_text("old")
            with patch("gm2b.storage.shutil.copytree", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    install_directory(src, target)
            self.assertEqual((target / "old").read_text(), "old")

    def test_successful_install_replaces_directory(self):
        with tempfile.TemporaryDirectory() as d:
            src, target = Path(d) / "src", Path(d) / "installed"
            src.mkdir(); target.mkdir()
            (src / "new").write_text("new")
            (target / "old").write_text("old")
            install_directory(src, target)
            self.assertFalse((target / "old").exists())
            self.assertEqual((target / "new").read_text(), "new")

    def test_export_options_reject_bad_values(self):
        for kwargs in ({"scale": float("nan")}, {"scale": 0}, {"formats": ()}, {"formats": ("exe",)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ExportOptions("out", "blender", **kwargs)

    def test_compiled_flex_matches_reference(self):
        mdl = SimpleNamespace(
            flex_controllers=[SimpleNamespace(name="smile", type="face", min=0, max=1)],
            flex_names=["smile_key"],
            flex_rules=[SimpleNamespace(flex=0, ops=[(FETCH1, 0, 0), (CONST, 0, 0.5), (MUL, 0, 0),
                                                      (CONST, 0, 0.1), (ADD, 0, 0)])])
        compiled = compile_flex(mdl)
        driver = compiled["drivers"]["smile_key"]
        for value in (0, 0.2, 1):
            expected = evaluate(mdl, {"smile": value})["smile_key"]
            actual = eval(driver["expr"], {"__builtins__": {}}, {"c0": value})
            self.assertAlmostEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
