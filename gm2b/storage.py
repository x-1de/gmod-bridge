"""Recoverable directory replacement for generated addons."""
from pathlib import Path
import shutil
import uuid


def preserve_existing(folder):
    """Retain previous generated output before a rebuild, even if compilation fails."""
    folder = Path(folder)
    if folder.exists():
        if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
            raise ValueError("Refusing to replace linked output")
        backup = folder.with_name(folder.name + ".previous-" + uuid.uuid4().hex[:8])
        folder.rename(backup)
        return backup
    return None


def install_directory(source, target):
    """Stage a complete copy before replacing an installation; keep the old copy on failure."""
    source, target = Path(source).resolve(), Path(target).absolute()
    if source == target.resolve() or source in target.resolve().parents or target.resolve() in source.parents:
        raise ValueError("Source and destination must not overlap")
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        raise ValueError("Refusing to replace a linked addon directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.name + ".stage-" + uuid.uuid4().hex)
    backup = target.with_name(target.name + ".backup-" + uuid.uuid4().hex)
    try:
        shutil.copytree(source, stage)
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except Exception:
            if backup.exists():
                backup.rename(target)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if backup.exists():
        shutil.rmtree(backup)
