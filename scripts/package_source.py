"""Create a source-only archive from an explicit allowlist (no user/game assets)."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ["README.md", "LICENSE", "CHANGELOG.md", "THIRD_PARTY.md", "CONTRIBUTING.md",
         "requirements.txt", "pyproject.toml", "MANIFEST.in", "run.py", ".gitignore"]


def main():
    out = ROOT / "dist" / "GModBridge-source.zip"
    out.parent.mkdir(exist_ok=True)
    files = [ROOT / name for name in FILES]
    for folder in ("gm2b", "scripts", "tests", ".github"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix in {".py", ".yml", ".md"})
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(files):
            archive.write(p, "gmod-bridge/" + p.relative_to(ROOT).as_posix())
    print(f"{out} ({len(files)} files)")


if __name__ == "__main__":
    main()
