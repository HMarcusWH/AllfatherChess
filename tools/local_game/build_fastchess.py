"""Build an exact Fastchess tree and bind it to an upstream-host source-test attestation."""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

from .common import ROOT, file_record, load, require, save, sha


def _os_release() -> dict[str, str]:
    values = {}
    path = Path("/etc/os-release")
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def _require_safe_build_path(path: Path) -> None:
    build_root = (ROOT / "build").resolve()
    require(path.resolve().is_relative_to(build_root),
            f"build output escapes build/: {path}")
    current = path if path.exists() else path.parent
    while current != ROOT / "build":
        require(not current.is_symlink(), f"symlinked build path component: {current}")
        current = current.parent


def _compiler() -> str:
    value = os.environ.get("CXX", "g++")
    require(value and not any(c.isspace() for c in value),
            "CXX must name one compiler executable")
    return value


def _checkout(lock: dict, parent: Path):
    temporary = tempfile.TemporaryDirectory(prefix="fastchess-", dir=parent)
    source = Path(temporary.name)

    def run(*args):
        subprocess.run(list(args), cwd=source, check=True)

    run("git", "init", "--quiet")
    run("git", "remote", "add", "origin", lock["repository"])
    run("git", "fetch", "--depth", "1", "origin", lock["commit"])
    run("git", "checkout", "--detach", "FETCH_HEAD")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=source, text=True).strip()

    require(git("rev-parse", "HEAD") == lock["commit"], "Fastchess commit mismatch")
    require(git("rev-parse", "HEAD^{tree}") == lock["tree"], "Fastchess tree mismatch")
    return temporary, source


def reset_build_target(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_build_path(target)
    require(not target.is_symlink(), "Fastchess target cannot be a symlink")
    if target.exists():
        require(target.is_dir(), "Fastchess target must be a directory")
        shutil.rmtree(target)
    target.mkdir()


def reset_attestation_target(attestation: Path) -> None:
    """Create the source-test destination without following stale build symlinks."""
    attestation = Path(attestation)
    _require_safe_build_path(attestation)
    parent = attestation.parent
    require(parent != ROOT / "build", "Fastchess attestation needs a dedicated build directory")
    require(not parent.is_symlink(), "Fastchess attestation directory cannot be a symlink")
    if parent.exists():
        require(parent.is_dir(), "Fastchess attestation parent must be a directory")
        shutil.rmtree(parent)
    parent.mkdir(parents=True)
    _require_safe_build_path(attestation)
    require(not attestation.exists(), "stale Fastchess source-test attestation survived reset")


def source_test(attestation: Path) -> int:
    release = _os_release()
    require(
        release.get("ID") == "ubuntu" and release.get("VERSION_ID") == "22.04",
        "Fastchess source-test attestation must be produced on Ubuntu 22.04",
    )
    lock_path = ROOT / "qualification/fastchess.lock.json"
    lock = load(lock_path)
    jobs = int(os.environ.get("JOBS", "2"))
    require(1 <= jobs <= 32, "JOBS outside build policy")
    compiler = _compiler()
    reset_attestation_target(attestation)
    parent = ROOT / "build"
    parent.mkdir(exist_ok=True)
    temporary, source = _checkout(lock, parent)
    try:
        subprocess.run(["make", "clean"], cwd=source, check=True)
        subprocess.run(
            ["make", f"-j{jobs}", "tests", "ZLIB=false", f"CXX={compiler}"],
            cwd=source,
            check=True,
        )
        subprocess.run([str(source / "fastchess-tests")], cwd=source, check=True)
        report = {
            "schema_version": 1,
            "passed": True,
            "repository": lock["repository"],
            "commit": lock["commit"],
            "tree": lock["tree"],
            "lock_sha256": sha(lock_path),
            "compiler": subprocess.check_output([compiler, "--version"], text=True),
            "platform": list(os.uname()),
            "os_release": {"ID": release.get("ID"), "VERSION_ID": release.get("VERSION_ID")},
            "reference_host": "ubuntu-22.04",
            "contract": "clean -> make tests -> fastchess-tests",
        }
        save(attestation, report)
    finally:
        temporary.cleanup()
    return 0


def build(attestation: Path) -> int:
    lock_path = ROOT / "qualification/fastchess.lock.json"
    lock = load(lock_path)
    target = ROOT / "build/tools/fastchess"
    reset_build_target(target)

    _require_safe_build_path(attestation)
    if not attestation.is_file():
        # Local reproduction may run source tests in the same host. CI supplies
        # the attestation from the upstream-supported Ubuntu 22.04 job.
        source_test(attestation)
    require(attestation.is_file() and not attestation.is_symlink(),
            "Fastchess source-test attestation must be a regular file")
    tested = load(attestation)
    require(tested.get("passed") is True and
            tested.get("commit") == lock["commit"] and
            tested.get("tree") == lock["tree"] and
            tested.get("lock_sha256") == sha(lock_path) and
            tested.get("reference_host") == "ubuntu-22.04" and
            (tested.get("os_release") or {}).get("ID") == "ubuntu" and
            (tested.get("os_release") or {}).get("VERSION_ID") == "22.04",
            "Fastchess source-test attestation does not match the frozen pin/reference host")

    jobs = int(os.environ.get("JOBS", "2"))
    require(1 <= jobs <= 32, "JOBS outside build policy")
    compiler = _compiler()
    temporary, source = _checkout(lock, target.parent)
    try:
        subprocess.run(["make", "clean"], cwd=source, check=True)
        subprocess.run(
            ["make", f"-j{jobs}", "build=release", "ZLIB=false",
             "NATIVE=-march=x86-64", f"CXX={compiler}"],
            cwd=source,
            check=True,
        )
        (target / "bin").mkdir(exist_ok=True)
        shutil.copy2(source / "fastchess", target / "bin/fastchess")
        shutil.copy2(source / "LICENSE", target / "LICENSE")
        shutil.copy2(attestation, target / "source-test-attestation.json")
        report = {
            "schema_version": 1,
            "source": lock,
            "lock_sha256": sha(lock_path),
            "upstream_tests_passed": True,
            "source_test_attestation": file_record(
                target / "source-test-attestation.json", target
            ),
            "compiler": subprocess.check_output([compiler, "--version"], text=True),
            "compiler_executable": compiler,
            "release_build": "clean -> make build=release ZLIB=false NATIVE=-march=x86-64",
            "binary": file_record(target / "bin/fastchess", target),
            "license": file_record(target / "LICENSE", target),
            "version": subprocess.check_output(
                [str(target / "bin/fastchess"), "-version"], text=True
            ),
        }
        save(target / "build-manifest.json", report)
    finally:
        temporary.cleanup()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-test-only", action="store_true")
    parser.add_argument(
        "--attestation",
        type=Path,
        default=ROOT / "build/fastchess-source-test/attestation.json",
    )
    args = parser.parse_args()
    attestation = args.attestation
    if not attestation.is_absolute():
        attestation = (Path.cwd() / attestation).absolute()
    require(attestation.is_relative_to(ROOT / "build"),
            "Fastchess attestation must live under build/")
    _require_safe_build_path(attestation)
    return source_test(attestation) if args.source_test_only else build(attestation)


if __name__ == "__main__":
    raise SystemExit(main())
