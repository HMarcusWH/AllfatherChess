"""S1 experimental Fastchess participant accounting with cgroup-v2 isolation.

Runs the EXISTING LOCAL-1 proxy without interpreting, changing or delaying UCI
messages: stdin/stdout/stderr are inherited as file descriptors.  The entire
proxy/controller/engine descendant tree is placed in a fresh, private cgroup
before exec.  The outside launcher reports its own CPU separately; it is never
double-counted in the cgroup counter.

No /proc PID-name reconstruction, inferred GPU/CPU exchange rate, same-compute
claim, or fallback pretending an unavailable measurement is zero.
This is test infrastructure only, not imported by the playing controller.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
import time
import uuid

from tools.local_game.common import (
    ROOT, load, process_identity, require, save, sha,
)


class ResourceScopeError(RuntimeError):
    """A strict accounting contract could not be established or verified."""


def _regular_control(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ResourceScopeError(f"missing cgroup control {path.name}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise ResourceScopeError(f"invalid or symlinked cgroup control: {path.name}")


def _open_control_write(path: Path) -> int:
    """Never follow a replaced symlink, and reject unwritable controls early."""
    _regular_control(path)
    flags = os.O_WRONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise ResourceScopeError(f"unwritable cgroup control {path.name}: {exc}") from exc


def _write_control(path: Path, value: bytes) -> None:
    fd = _open_control_write(path)
    try:
        if os.write(fd, value) != len(value):
            raise ResourceScopeError(f"partial cgroup control write: {path.name}")
    finally:
        os.close(fd)


def _read_nonnegative(path: Path) -> int:
    _regular_control(path)
    raw = path.read_text(encoding="ascii").strip()
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ResourceScopeError(f"invalid cgroup scalar: {path.name}")
    value = int(raw)
    if value < 0:
        raise ResourceScopeError(f"negative cgroup scalar: {path.name}")
    return value


def _read_stat(path: Path, *, required: tuple[str, ...]) -> dict[str, int]:
    _regular_control(path)
    result: dict[str, int] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in result or not fields[1].isascii() \
                or not fields[1].isdecimal():
            raise ResourceScopeError(f"malformed/duplicate cgroup counter: {path.name}")
        result[fields[0]] = int(fields[1])
    if not all(key in result for key in required):
        raise ResourceScopeError(f"missing cgroup counter: {path.name}")
    return result


def read_cgroup_counters(directory: Path) -> dict:
    """Read kernel counters, never estimate missing values or accept symlinks."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ResourceScopeError("cgroup directory absent or symlinked")
    cpu = _read_stat(directory / "cpu.stat", required=("usage_usec",))
    events = _read_stat(directory / "cgroup.events", required=("populated",))
    if events["populated"] not in (0, 1):
        raise ResourceScopeError("unknown cgroup population value")
    return {
        "cpu_usage_usec": cpu["usage_usec"],
        "memory_peak_bytes": _read_nonnegative(directory / "memory.peak"),
        "memory_current_bytes": _read_nonnegative(directory / "memory.current"),
        "populated": events["populated"],
    }


def membership_parent(mount: Path, membership_text: str) -> Path:
    """Resolve a unified v2 membership, refusing traversal and hybrid cgroups."""
    lines = [line for line in membership_text.splitlines() if line]
    if len(lines) != 1 or not lines[0].startswith("0::"):
        raise ResourceScopeError("not an unambiguous unified cgroup-v2 membership")
    raw = lines[0][3:]
    if not raw.startswith("/") or "\x00" in raw:
        raise ResourceScopeError("invalid cgroup membership path")
    parts = raw.lstrip("/").split("/") if raw != "/" else []
    if any(part in ("", ".", "..") for part in parts):
        raise ResourceScopeError("unsafe cgroup membership path")
    root = mount.resolve(strict=True)
    if not (root / "cgroup.controllers").is_file():
        raise ResourceScopeError("unified cgroup-v2 mount not detected")
    candidate = root.joinpath(*parts)
    if candidate.resolve(strict=True) != candidate or not candidate.is_relative_to(root):
        raise ResourceScopeError("symlinked/escaped cgroup membership")
    if candidate.is_symlink() or not candidate.is_dir():
        raise ResourceScopeError("membership cgroup is not a directory")
    return candidate


class ParticipantCgroup:
    """One newly allocated cgroup; never attach unrelated or reused PIDs."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.inode = directory.stat().st_ino
        self.baseline = read_cgroup_counters(directory)
        if self.baseline["populated"] != 0 or self.baseline["cpu_usage_usec"] != 0:
            raise ResourceScopeError("new cgroup was already active")

    @classmethod
    def create(cls) -> "ParticipantCgroup":
        mount = Path("/sys/fs/cgroup")
        try:
            parent = membership_parent(
                mount, Path("/proc/self/cgroup").read_text(encoding="ascii"))
            name = "allfather-s1-" + uuid.uuid4().hex
            directory = parent / name
            directory.mkdir(mode=0o700)
        except (OSError, ValueError, ResourceScopeError) as exc:
            raise ResourceScopeError(f"no delegated cgroup-v2 scope: {exc}") from exc
        try:
            for control in ("cgroup.procs", "cgroup.kill", "cpu.stat",
                            "cgroup.events", "memory.peak", "memory.current"):
                _regular_control(directory / control)
            # Delegated write authority must be established BEFORE a chess
            # process is launched, never discovered only during cleanup.
            for control in ("cgroup.procs", "cgroup.kill"):
                fd = _open_control_write(directory / control)
                os.close(fd)
            return cls(directory)
        except BaseException:
            try:
                directory.rmdir()
            except OSError:
                pass
            raise

    def _check_identity(self) -> None:
        if self.directory.is_symlink() or self.directory.stat().st_ino != self.inode:
            raise ResourceScopeError("cgroup identity changed or was replaced")

    def join_in_child(self) -> None:
        """Only called by subprocess preexec before the child executes Python."""
        self._check_identity()
        _write_control(
            self.directory / "cgroup.procs", str(os.getpid()).encode("ascii"))

    def counters(self) -> dict:
        self._check_identity()
        measured = read_cgroup_counters(self.directory)
        if measured["cpu_usage_usec"] < self.baseline["cpu_usage_usec"]:
            raise ResourceScopeError("cgroup CPU counter reversed")
        measured["cpu_usage_usec"] -= self.baseline["cpu_usage_usec"]
        return measured

    def kill_if_populated(self) -> bool:
        """Return whether live/leaked work was observed BEFORE emergency cleanup."""
        if self.counters()["populated"] == 0:
            return False
        self._check_identity()
        _write_control(self.directory / "cgroup.kill", b"1")
        limit = time.monotonic() + 5.0
        while self.counters()["populated"] and time.monotonic() < limit:
            time.sleep(0.05)
        return True

    def remove(self) -> None:
        self._check_identity()
        if self.counters()["populated"]:
            raise ResourceScopeError("cannot remove a populated participant scope")
        self.directory.rmdir()


def _session_names(folder: Path) -> set[str]:
    if not folder.exists():
        return set()
    if folder.is_symlink() or not folder.is_dir():
        raise ResourceScopeError("unsafe proxy session root")
    names = set()
    for child in folder.iterdir():
        if child.is_symlink() or not child.is_dir():
            raise ResourceScopeError("non-session object in session root")
        names.add(child.name)
    return names


def receipt_location(spec: dict, scope_id: str) -> Path:
    root = Path(spec["root"]).resolve(strict=True)
    sessions = Path(spec["sessions"]).resolve()
    if not sessions.is_relative_to(root / "build") or sessions.parent.name != "sessions":
        raise ResourceScopeError("participant receipts require job-owned build sessions")
    arm = spec["arm"]
    if not isinstance(arm, str) or not arm or "/" in arm or arm in (".", ".."):
        raise ResourceScopeError("unsafe participant name")
    return sessions.parent.parent / "participant_resources" / arm / (scope_id + ".json")


def command_for_proxy(spec_path: Path) -> list[str]:
    return [sys.executable, "-m", "tools.local_game.proxy", "--spec", str(spec_path)]


def launch(spec_path: Path) -> int:
    spec_path = spec_path.resolve(strict=True)
    spec = load(spec_path)
    require(type(spec) is dict and spec.get("schema_version") == 1,
            "invalid participant proxy spec")
    require(set(spec) == {"schema_version", "arm", "root", "sessions",
                          "environment", "source_runtime"},
            "unrecognized participant proxy specification")
    require(spec.get("root") == str(ROOT), "participant root mismatch")
    require(spec_path.is_relative_to(ROOT / "build"),
            "participant proxy spec must be in build")
    scope_id = uuid.uuid4().hex
    receipt_path = receipt_location(spec, scope_id)
    folder = Path(spec["sessions"])
    before = _session_names(folder)
    started_ns = time.monotonic_ns()
    errors: list[str] = []
    child = None
    identity = None
    cgroup: ParticipantCgroup | None = None
    counters = None
    leaked = False
    returncode = None

    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f"participant wrapper interrupted by {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        cgroup = ParticipantCgroup.create()  # strict: no procfs/rusage fallback
        child = subprocess.Popen(
            command_for_proxy(spec_path),
            cwd=ROOT,
            env=os.environ.copy(),
            stdin=sys.stdin.buffer,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
            start_new_session=True,
            preexec_fn=cgroup.join_in_child,
            bufsize=0,
        )
        identity = process_identity(child.pid)
        if identity is None or identity.get("pgid") != child.pid:
            raise ResourceScopeError("proxy child identity unavailable or non-private")
        returncode = child.wait()
        if returncode != 0:
            errors.append(f"proxy failed with return code {returncode}")
    except BaseException as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        # Emergency cleanup may make the machine safe; it never retroactively
        # converts a participant which leaked descendants into a passing one.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if cgroup is not None:
            try:
                leaked = cgroup.kill_if_populated()
                if leaked:
                    errors.append("participant descendants survived proxy termination")
                counters = cgroup.counters()
                if counters["populated"]:
                    errors.append("cgroup remains populated after bounded cleanup")
                else:
                    cgroup.remove()
            except BaseException as exc:
                errors.append(f"cgroup cleanup: {type(exc).__name__}: {exc}")
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                errors.append("proxy not reaped after cleanup")
            if returncode is None:
                returncode = child.returncode
        try:
            after = _session_names(folder)
            new = after - before
            if len(new) != 1:
                raise ResourceScopeError("participant must produce exactly one fresh session")
            session_id = next(iter(new))
            session_path = folder / session_id / "session.json"
            session = load(session_path)
            if session.get("status") != "completed" or session.get("returncode") != 0:
                raise ResourceScopeError("participant session not successfully finalized")
            session_digest = sha(session_path)
        except BaseException as exc:
            errors.append(f"session identity: {type(exc).__name__}: {exc}")
            session_id = None
            session_digest = None

        # Launcher is outside the child cgroup. This cumulative self-rusage
        # includes Python startup and wrapper cleanup; it excludes all children.
        self_usage = resource.getrusage(resource.RUSAGE_SELF)
        wrapper_cpu_ms = (self_usage.ru_utime + self_usage.ru_stime) * 1000
        wrapper_peak_kib = int(self_usage.ru_maxrss)  # Linux ru_maxrss = KiB
        measured = counters is not None and not errors
        receipt = {
            "schema_version": 1,
            "accounting": "strict_cgroup_v2_child_tree_plus_launcher",
            "status": "MEASURED" if measured else "FAILED",
            "scope_id": scope_id,
            "arm": spec["arm"],
            "session_id": session_id,
            "session_sha256": session_digest,
            "proxy_pid": child.pid if child else None,
            "proxy_identity": identity,
            "proxy_returncode": returncode,
            "started_ns": started_ns,
            "ended_ns": time.monotonic_ns(),
            "observed_leak_before_cleanup": leaked,
            "errors": errors,
            "cpu_ms": (
                counters["cpu_usage_usec"] / 1000.0 + wrapper_cpu_ms
                if counters is not None else None
            ),
            "cgroup_child_tree_cpu_ms": (
                counters["cpu_usage_usec"] / 1000.0 if counters else None
            ),
            "launcher_self_cpu_ms": wrapper_cpu_ms,
            "cgroup_memory_peak_bytes": (
                counters["memory_peak_bytes"] if counters else None
            ),
            "launcher_peak_rss_kib": wrapper_peak_kib,
            "participant_peak_memory_upper_bound_bytes": (
                counters["memory_peak_bytes"] + wrapper_peak_kib * 1024
                if counters else None
            ),
            "peak_is_exact": False,  # two disjoint scopes, conservative sum
            "same_compute_claim": False,
            "strength_qualified": False,
            "measurement_boundary": "through post-proxy cleanup / pre-receipt seal",
        }
        save(receipt_path, receipt)
    return 0 if receipt["status"] == "MEASURED" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    return launch(args.spec)


if __name__ == "__main__":
    raise SystemExit(main())
