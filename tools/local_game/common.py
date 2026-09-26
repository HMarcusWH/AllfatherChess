"""Shared LOCAL-1 contracts. Test tooling; never imported by the playing engine."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ARMS = ("stockfish", "reckless", "lc0", "allfather-anchor", "allfather-g3")


class QualificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationError(message)


def _object(pairs):
    result = {}
    for k, v in pairs:
        require(k not in result, f"duplicate JSON key: {k}")
        result[k] = v
    return result


def load(path: Path) -> Any:
    def reject(value):
        raise QualificationError(f"nonfinite JSON: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=reject, object_pairs_hook=_object)


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def file_record(path: Path, root: Path = ROOT) -> dict:
    return {"path": str(path.resolve().relative_to(root.resolve())),
            "sha256": sha(path), "bytes": path.stat().st_size}


def contained(root: Path, relative: str) -> Path:
    require(isinstance(relative, str) and bool(relative), "empty artifact path")
    p = Path(relative)
    require(not p.is_absolute() and ".." not in p.parts, "unsafe artifact path")
    result = (root / p).resolve()
    require(result.is_relative_to(root.resolve()), "artifact escapes its bundle")
    return result


def verify_record(root: Path, record: dict) -> Path:
    path = contained(root, record["path"])
    require(path.is_file(), f"missing artifact {path}")
    require(sha(path) == record["sha256"] and path.stat().st_size == record["bytes"],
            f"artifact changed: {path}")
    return path


def policy(root: Path = ROOT) -> dict:
    p = load(root / "qualification/local-full-game.json")
    require(type(p.get("schema_version")) is int and p["schema_version"] == 1,
            "unsupported LOCAL-1 policy")
    require(p.get("arms") == list(ARMS), "the five baseline arms must not change implicitly")
    require(type(p.get("concurrency")) is int and p["concurrency"] == 1 and
            type(p.get("games_per_pair")) is int and p["games_per_pair"] == 2,
            "LOCAL-1 requires serial, color-reversed pairs")
    require(p.get("same_compute_claim") is False, "LOCAL-1 is not equal-compute qualification")
    return p


def runtime_config(source: dict, arm: str, root: Path, replay: Path) -> dict:
    """No duplicated G3 policy: derive only output paths, or the explicit native-clock control."""
    require(arm in ("allfather-g3", "allfather-anchor"), "not a controller arm")
    if arm == "allfather-g3":
        config = copy.deepcopy(source)
        config["root"] = str(root.resolve())
        config["shadow"]["replay_root"] = str(replay.resolve())
        return config
    return {"schema_version": 2, "mode": "anchor", "anchor": "stockfish-anchor",
            "root": str(root.resolve()),
            "instances": {"stockfish-anchor": copy.deepcopy(source["instances"]["stockfish-anchor"])}}


def verify_g3_derivation(source: dict, candidate: dict, root: Path, replay: Path) -> None:
    require(candidate == runtime_config(source, "allfather-g3", root, replay),
            "G3 runtime differs beyond declared root/replay output relocation")


def source_identity(root: Path = ROOT) -> dict:
    require(
        subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--"],
            check=False,
        ).returncode == 0,
        "tracked checkout is dirty; qualify committed source only",
    )

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
        ).strip()

    # A clean tracked diff is insufficient: an untracked sitecustomize.py,
    # importable helper, or fixture can change qualification behavior while the
    # manifest still claims pristine HEAD. Respect .gitignore so generated
    # build evidence does not make qualification impossible, but fail closed on
    # every other untracked path.
    untracked = [
        line
        for line in git("ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip()
    ]
    require(
        not untracked,
        "untracked source/fixture files present; qualify committed source only: "
        + ", ".join(untracked[:20]),
    )
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}")}


def process_group(pgid: int) -> dict[str, dict]:
    """Linux procfs identity, not process-name matching. CPU is a cumulative endpoint."""
    result = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            text = (directory / "stat").read_text()
            fields = text[text.rfind(")") + 2:].split()
            if int(fields[2]) != pgid or fields[0] == "Z":
                continue
            pid, start = int(directory.name), int(fields[19])
            result[f"{pid}:{start}"] = {
                "pid": pid, "start_ticks": start,
                "cpu_ms": (int(fields[11]) + int(fields[12])) * 1000 / os.sysconf("SC_CLK_TCK"),
                "rss_kib": int(fields[21]) * os.sysconf("SC_PAGE_SIZE") / 1024,
            }
        except (FileNotFoundError, ProcessLookupError):
            continue
    return result


def cpu_delta(before: dict, after: dict) -> float | None:
    # A process exiting/reappearing between endpoints is missing attribution, never zero.
    if not before or set(before) != set(after):
        return None
    values = [after[k]["cpu_ms"] - before[k]["cpu_ms"] for k in before]
    return sum(values) if all(v >= 0 for v in values) else None
