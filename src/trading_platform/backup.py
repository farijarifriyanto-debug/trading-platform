import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)


def create_backup(data_root: str | Path, archive: str | Path) -> Path:
    data_root = Path(data_root)
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trading-backup-") as temp:
        stage = Path(temp) / "data"
        stage.mkdir()
        if data_root.exists():
            for item in data_root.iterdir():
                if item.name.endswith(("-wal", "-shm")) or item.name == ".ready-probe":
                    continue
                target = stage / item.name
                if item.name in {"runtime.sqlite3", "jobs.sqlite3", "live.sqlite3"} and item.is_file():
                    _sqlite_snapshot(item, target)
                    if item.name == "live.sqlite3":
                        with sqlite3.connect(target) as conn:
                            conn.execute(
                                "UPDATE live_control SET kill_switch=1, armed_until=NULL WHERE id=1"
                            )
                elif item.is_dir():
                    shutil.copytree(item, target)
                elif item.is_file():
                    shutil.copy2(item, target)

        files = sorted(path for path in stage.rglob("*") if path.is_file())
        manifest = {
            "schema_version": 1,
            "files": {
                path.relative_to(stage).as_posix(): {
                    "sha256": _sha256(path),
                    "size": path.stat().st_size,
                }
                for path in files
            },
        }
        (stage / "BACKUP_MANIFEST.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temp_archive = archive.with_suffix(archive.suffix + ".tmp")
        with tarfile.open(temp_archive, "w:gz") as tar:
            tar.add(stage, arcname="data")
        temp_archive.replace(archive)
    verify_backup(archive)
    return archive


def _safe_members(tar: tarfile.TarFile):
    for member in tar.getmembers():
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe backup member path")
        yield member


def verify_backup(archive: str | Path) -> dict[str, object]:
    archive = Path(archive)
    with tarfile.open(archive, "r:gz") as tar:
        members = list(_safe_members(tar))
        manifest_member = tar.getmember("data/BACKUP_MANIFEST.json")
        manifest_file = tar.extractfile(manifest_member)
        if manifest_file is None:
            raise ValueError("backup manifest missing")
        manifest = json.loads(manifest_file.read())
        by_name = {member.name: member for member in members}
        for relative, expected in manifest["files"].items():
            name = f"data/{relative}"
            member = by_name.get(name)
            if member is None:
                raise ValueError(f"backup file missing: {relative}")
            handle = tar.extractfile(member)
            if handle is None:
                raise ValueError(f"backup file unreadable: {relative}")
            raw = handle.read()
            if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
                raise ValueError(f"backup checksum mismatch: {relative}")
            if len(raw) != expected["size"]:
                raise ValueError(f"backup size mismatch: {relative}")
    return {"ok": True, "files": len(manifest["files"]), "schema_version": manifest["schema_version"]}


def restore_backup(archive: str | Path, destination: str | Path, *, require_empty: bool = True) -> Path:
    verify_backup(archive)
    destination = Path(destination)
    if require_empty and destination.exists() and any(destination.iterdir()):
        raise ValueError("restore destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trading-restore-") as temp:
        stage = Path(temp)
        with tarfile.open(archive, "r:gz") as tar:
            members = list(_safe_members(tar))
            tar.extractall(stage, members=members)
        restored = stage / "data"
        for item in restored.iterdir():
            if item.name == "BACKUP_MANIFEST.json":
                continue
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    return destination
