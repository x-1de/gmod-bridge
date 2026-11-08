"""Verify Blender-to-Source conversion using an original synthetic cube, without game assets."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    blender = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="gm2b_smoke_") as d:
        folder = Path(d)
        fixture = folder / "cube.blend"
        expression = (
            "import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); "
            "bpy.ops.mesh.primitive_cube_add(); "
            f"bpy.ops.wm.save_as_mainfile(filepath={str(fixture)!r})"
        )
        subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1",
                        "--python-expr", expression], check=True)
        job = folder / "job.json"
        job.write_text(json.dumps({"input": str(fixture), "out_dir": str(folder), "scale": 1,
                                   "rotate": 0}), encoding="utf-8")
        subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python",
                        str(ROOT / "gm2b" / "blender_to_source.py"), "--", str(job)], check=True)
        info = json.loads((folder / "source.json").read_text(encoding="utf-8"))
        assert info["triangles"] == 12, info
        for name in ("ref.smd", "phys.smd"):
            assert "triangles" in (folder / name).read_text(encoding="utf-8")
        print("Blender smoke: cube mesh, materials and collision sources generated")


if __name__ == "__main__":
    main()
