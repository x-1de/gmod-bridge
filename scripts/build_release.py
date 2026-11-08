"""Run with the project's Python: python scripts/build_release.py."""
from pathlib import Path
import hashlib
import importlib.metadata
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gm2b import __version__


def main():
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, check=True)
    environment = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(map(str, [Path(sys.executable).parent,
                                                   Path(sys.base_prefix) / "bin", windows / "System32", windows]))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                    "--name", "GModBridge", "--collect-all", "gm2b", "--distpath", str(ROOT / "dist"),
                    "--specpath", str(ROOT / "build"), str(ROOT / "run.py")], cwd=ROOT, check=True, env=environment)
    folder = ROOT / "dist" / "GModBridge"
    report = ROOT / "build" / "frozen-smoke.json"
    report.unlink(missing_ok=True)
    subprocess.run([str(folder / "GModBridge.exe"), "--smoke-test", str(report)], check=True, timeout=120)
    if not report.is_file():
        raise RuntimeError("Packaged application did not create its smoke-test report")
    for name in ("README.md", "LICENSE", "THIRD_PARTY.md", "CHANGELOG.md"):
        shutil.copy2(ROOT / name, folder / name)
    if (ROOT / "docs").is_dir():
        shutil.copytree(ROOT / "docs", folder / "docs", dirs_exist_ok=True)
    notices = folder / "THIRD_PARTY_LICENSES"
    notices.mkdir(exist_ok=True)
    dependencies = ["PySide6_Essentials", "shiboken6", "Pillow", "numpy", "pyinstaller"]
    for name in dependencies:
        distribution = importlib.metadata.distribution(name)
        destination = notices / name
        destination.mkdir(exist_ok=True)
        (destination / "METADATA.txt").write_text(distribution.read_text("METADATA") or "", encoding="utf-8")
        for entry in distribution.files or []:
            if any(word in str(entry).lower() for word in ("license", "licence", "copying", "notice")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    # Package-relative paths may contain '..'; flatten safely.
                    target = destination / str(entry).replace("/", "_").replace("\\", "_").replace("..", "_")
                    shutil.copy2(source, target)
    for name in ("LICENSE.txt", "LICENSE"):
        source = Path(sys.base_prefix) / name
        if source.is_file():
            shutil.copy2(source, notices / "Python-LICENSE.txt")
    archive = Path(shutil.make_archive(str(ROOT / "dist" / f"GModBridge-{__version__}-windows-x64"),
                                       "zip", folder.parent, folder.name))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)


if __name__ == "__main__":
    main()
