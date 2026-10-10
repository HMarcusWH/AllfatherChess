#!/usr/bin/env python3
"""Create a deterministic, relocatable archive of independently sealed release bytes."""
from __future__ import annotations
import argparse
import gzip
import importlib.util
import json
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "build/online-release/release-manifest.json"


def seal_module(root: Path):
    spec = importlib.util.spec_from_file_location("allfather_seal", root / "scripts/release-manifest.py")
    if spec is None or spec.loader is None:
        raise ValueError("release seal module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package(root: Path, stage: Path, archive: Path, manifest: Path) -> dict:
    root = root.resolve()
    dist = root / "dist"
    if not stage.resolve().is_relative_to(dist.resolve()) or stage == dist:
        raise ValueError("stage must be inside dist/")
    if not archive.resolve().is_relative_to(dist.resolve()) or archive == stage:
        raise ValueError("archive must be inside dist/")
    if stage.exists() or stage.is_symlink() or archive.exists() or archive.is_symlink():
        raise ValueError("refusing to overwrite an existing release")
    seal = seal_module(root)
    record = seal.verify(root, manifest)
    paths = sorted(set(
        [row["path"] for row in record["source_files"]]
        + [row["path"] for row in record["bundle_artifacts"]]
        + [record["runtime_config"]["path"], record["build_manifest"]["path"],
           manifest.relative_to(root).as_posix()]
    ))
    try:
        for name in paths:
            source = seal.safe(root, name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if name.startswith("build/online-engine-opt-v2/bin/")
                         or name == "deploy/bin/allfather-online" else 0o644)
        seal.verify(stage, stage / manifest.relative_to(root))
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(mode="w", fileobj=compressed) as tar:
                for name in paths:
                    path = stage / name
                    item = tar.gettarinfo(str(path), arcname=name)
                    item.uid = item.gid = item.mtime = 0
                    item.uname = item.gname = "root"
                    with path.open("rb") as source:
                        tar.addfile(item, source)
        return {"stage": str(stage), "archive": str(archive), "source": record["source_commit"],
                "file_count": len(paths), "sha256": seal.sha(archive)}
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if archive.exists():
            archive.unlink()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=Path(MANIFEST))
    parser.add_argument("--stage", type=Path, default=Path("dist/online3a-stage"))
    parser.add_argument("--archive", type=Path, default=Path("dist/allfather-online3a-offline.tar.gz"))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    def path(value):
        return value if value.is_absolute() else root / value
    try:
        result = package(root, path(args.stage), path(args.archive), path(args.manifest))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, "ONLINE-3A package rejected: " + str(exc) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
