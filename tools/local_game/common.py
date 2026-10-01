"""Shared LOCAL-1 contracts. Test tooling; never imported by the playing engine."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import signal
import stat
import time
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ARMS = ("stockfish", "reckless", "lc0", "allfather-anchor", "allfather-g3")
ORCHESTRATED_ARMS = (
    "stockfish",
    "reckless",
    "lc0",
    "allfather-anchor",
    "allfather-orchestrated",
)
AUTHORITY_ARMS = ("allfather-g3", "allfather-orchestrated")


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


def regular_tree_digest(root: Path) -> str:
    """Digest a regular-file tree by sorted relative path, byte size and file SHA."""
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), f"unsafe evidence root: {root}")
    digest = hashlib.sha256()
    for current_raw, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_raw)
        directories.sort()
        filenames.sort()
        for name in directories:
            path = current / name
            mode = path.lstat().st_mode
            require(stat.S_ISDIR(mode) and not stat.S_ISLNK(mode),
                    f"non-directory/symlink in evidence tree: {path}")
            relative = path.relative_to(root).as_posix()
            digest.update(f"D\\0{relative}\\0".encode("utf-8"))
        for name in filenames:
            path = current / name
            mode = path.lstat().st_mode
            require(stat.S_ISREG(mode), f"special file in evidence tree: {path}")
            relative = path.relative_to(root).as_posix()
            digest.update(f"F\\0{relative}\\0{path.stat().st_size}\\0{sha(path)}\\0".encode("utf-8"))
    return digest.hexdigest()


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


def policy(root: Path = ROOT, path: Path | str | None = None) -> dict:
    selected = root / "qualification/local-full-game.json" if path is None else Path(path)
    if not selected.is_absolute():
        selected = root / selected
    selected = selected.resolve()
    require(selected.is_relative_to(root.resolve()), "LOCAL-1 policy escapes repository root")
    p = load(selected)
    require(type(p.get("schema_version")) is int and p["schema_version"] == 1,
            "unsupported LOCAL-1 policy")
    expected_arms = (
        ORCHESTRATED_ARMS
        if p.get("profile_id") == "local-full-game-orchestrated-v1"
        else ARMS
    )
    require(
        p.get("arms") == list(expected_arms),
        "the five baseline arms must match the declared lifecycle profile",
    )
    expected_controller = (
        "allfather-orchestrated"
        if expected_arms == ORCHESTRATED_ARMS
        else "allfather-g3"
    )
    require(
        p.get("controller_arm", expected_controller) == expected_controller,
        "LOCAL-1 controller arm differs from the declared lifecycle profile",
    )
    require(type(p.get("concurrency")) is int and p["concurrency"] == 1 and
            type(p.get("games_per_pair")) is int and p["games_per_pair"] == 2,
            "LOCAL-1 requires serial, color-reversed pairs")
    require(p.get("same_compute_claim") is False, "LOCAL-1 is not equal-compute qualification")
    return p


def controller_arm(p: dict) -> str:
    return str(p.get("controller_arm", "allfather-g3"))


def is_authority_arm(arm: str) -> bool:
    return arm in AUTHORITY_ARMS


def runtime_config(source: dict, arm: str, root: Path, replay: Path) -> dict:
    """Derive controller arms only by relocating root/replay output paths."""
    require(
        arm in (*AUTHORITY_ARMS, "allfather-anchor"),
        "not a controller arm",
    )
    if arm in AUTHORITY_ARMS:
        config = copy.deepcopy(source)
        config["root"] = str(root.resolve())
        config["shadow"]["replay_root"] = str(replay.resolve())
        return config
    return {"schema_version": 2, "mode": "anchor", "anchor": "stockfish-anchor",
            "root": str(root.resolve()),
            "instances": {"stockfish-anchor": copy.deepcopy(source["instances"]["stockfish-anchor"])}}


def verify_controller_derivation(
    source: dict,
    candidate: dict,
    arm: str,
    root: Path,
    replay: Path,
) -> None:
    require(
        arm in AUTHORITY_ARMS,
        "controller derivation verifier requires an authority arm",
    )
    require(
        candidate == runtime_config(source, arm, root, replay),
        f"{arm} runtime differs beyond declared root/replay output relocation",
    )


def verify_g3_derivation(source: dict, candidate: dict, root: Path, replay: Path) -> None:
    verify_controller_derivation(source, candidate, "allfather-g3", root, replay)


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
    # Fixture suffixes such as *.pgn/*.epd are globally ignored, so ask Git
    # explicitly for ignored-but-untracked files in the qualification fixture
    # tree as well.
    ignored_fixtures = [
        line
        for line in git(
            "ls-files", "--others", "--ignored", "--exclude-standard", "--",
            "tests/fixtures/local_full_game",
        ).splitlines()
        if line.strip()
    ]
    untracked.extend(path for path in ignored_fixtures if path not in untracked)
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


def process_identity(pid: int) -> dict | None:
    """Return stable Linux process identity; zombies are not live work."""
    try:
        stat_text = (Path("/proc") / str(pid) / "stat").read_text()
        close = stat_text.rfind(")")
        require(close >= 0, f"malformed /proc/{pid}/stat")
        fields = stat_text[close + 2:].split()
        state = fields[0]
        if state == "Z":
            return None
        return {
            "pid": int(pid),
            "state": state,
            "ppid": int(fields[1]),
            "pgid": int(fields[2]),
            "start_ticks": int(fields[19]),
        }
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None


def processes_with_token(token: str) -> list[dict]:
    """Discover live processes inheriting one LOCAL-1 ownership token."""
    require(isinstance(token, str) and token, "empty qualification process token")
    needle = f"ALLFATHER_LOCAL1_PROCESS_TOKEN={token}".encode()
    found = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        pid = int(directory.name)
        identity = process_identity(pid)
        if identity is None:
            continue
        try:
            environ = (directory / "environ").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if needle in environ:
            found.append(identity)
    return sorted(found, key=lambda row: (row["pgid"], row["pid"], row["start_ticks"]))


def terminate_token_processes(token: str, *, term_s: float = 5.0, kill_s: float = 5.0
                              ) -> tuple[list[dict], list[dict]]:
    """Observe exact token-owned live work, then clean it without PID reuse risk."""
    before = processes_with_token(token)
    if not before:
        return [], []

    def signal_current(signum: int) -> list[dict]:
        rows = processes_with_token(token)
        for row in rows:
            identity = process_identity(row["pid"])
            if identity is None or identity["start_ticks"] != row["start_ticks"]:
                continue
            try:
                os.kill(row["pid"], signum)
            except ProcessLookupError:
                pass
        return rows

    signal_current(signal.SIGTERM)
    deadline = time.monotonic() + term_s
    remaining = processes_with_token(token)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.05)
        remaining = processes_with_token(token)

    if remaining:
        deadline = time.monotonic() + kill_s
        while remaining and time.monotonic() < deadline:
            signal_current(signal.SIGKILL)
            time.sleep(0.05)
            remaining = processes_with_token(token)
    return before, remaining

def safe_copy_regular_tree(source: Path, destination: Path) -> None:
    """Copy one evidence tree without following links or accepting special files."""
    source = Path(source)
    destination = Path(destination)
    require(source.is_dir() and not source.is_symlink(), f"unsafe evidence root: {source}")
    require(not destination.exists() and not destination.is_symlink(),
            f"evidence destination already exists: {destination}")
    destination.mkdir(parents=True)
    source_digest = regular_tree_digest(source)

    def copy_dir(src: Path, dst: Path) -> None:
        with os.scandir(src) as entries:
            for entry in entries:
                src_path = Path(entry.path)
                dst_path = dst / entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                require(not stat.S_ISLNK(mode), f"symlink in evidence tree: {src_path}")
                if stat.S_ISDIR(mode):
                    dst_path.mkdir()
                    copy_dir(src_path, dst_path)
                elif stat.S_ISREG(mode):
                    shutil.copyfile(src_path, dst_path, follow_symlinks=False)
                    shutil.copystat(src_path, dst_path, follow_symlinks=False)
                    require(sha(src_path) == sha(dst_path) and
                            src_path.stat().st_size == dst_path.stat().st_size,
                            f"evidence copy changed bytes: {src_path}")
                else:
                    raise QualificationError(f"special file in evidence tree: {src_path}")

    copy_dir(source, destination)
    require(regular_tree_digest(source) == source_digest,
            "evidence source changed while it was being retained")
    require(regular_tree_digest(destination) == source_digest,
            "retained evidence tree digest differs from source")
