"""Build/test an exact Fastchess tree into a separate, content-recorded bundle."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from .common import ROOT, file_record, load, require, save, sha


def main() -> int:
    lock_path = ROOT / "qualification/fastchess.lock.json"
    lock = load(lock_path)
    target = ROOT / "build/tools/fastchess"
    target.parent.mkdir(parents=True, exist_ok=True)
    require(not target.is_symlink(), "Fastchess target cannot be a symlink")
    target.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fastchess-", dir=target.parent) as temporary:
        source = Path(temporary)
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
        jobs = int(os.environ.get("JOBS", "2"))
        require(1 <= jobs <= 32, "JOBS outside build policy")
        run("make", f"-j{jobs}", "ZLIB=false")
        run("make", f"-j{jobs}", "tests", "ZLIB=false")
        run(str(source / "fastchess-tests"))
        (target / "bin").mkdir(exist_ok=True)
        shutil.copy2(source / "fastchess", target / "bin/fastchess")
        shutil.copy2(source / "LICENSE", target / "LICENSE")
        report = {"schema_version": 1, "source": lock, "lock_sha256": sha(lock_path),
                  "upstream_tests_passed": True,
                  "compiler": subprocess.check_output([os.environ.get("CXX", "g++"), "--version"], text=True),
                  "binary": file_record(target / "bin/fastchess", target),
                  "license": file_record(target / "LICENSE", target),
                  "version": subprocess.check_output([str(target / "bin/fastchess"), "-version"], text=True)}
        save(target / "build-manifest.json", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
