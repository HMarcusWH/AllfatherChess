"""Frozen serial lifecycle + five-arm round robin. Fastchess owns the games."""
from __future__ import annotations

import argparse
import itertools
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

from .common import (ARMS, ROOT, contained, file_record, load, policy, require, save,
                     sha, source_identity, verify_record)
from .integrity import input_paths, verify_builds


def schedule(p: dict, mode: str) -> list[dict]:
    require(mode in ("required", "soak"), "unsupported campaign mode")
    jobs = []
    for case in p["lifecycle_cases"]:
        jobs.append({"id": "life-" + case["id"], "kind": "lifecycle",
                     "arms": ["allfather-g3", "stockfish"], "clock": p[case["clock"]],
                     "opening": case["opening"], "restart": case["restart"],
                     "driver_nodes": p["reference_driver_nodes"],
                     "allow_resource_denial": case["id"] == "low-clock"})
    repeats = 1 if mode == "required" else p["soak_repetitions"]
    for repeat in range(repeats):
        for index, pair in enumerate(itertools.combinations(ARMS, 2)):
            jobs.append({"id": f"base-{repeat:02d}-{index:02d}", "kind": "baseline",
                         "arms": list(pair), "clock": p["baseline_clock"],
                         "opening": "history.pgn", "restart": False,
                         "driver_nodes": None, "allow_resource_denial": False})
    return jobs


def engine_options(arm: str, source: dict) -> tuple[dict, dict]:
    if arm.startswith("allfather-"):
        return {"UCI_Chess960": False}, {}
    name = {"stockfish": "stockfish-anchor", "reckless": "reckless-shadow", "lc0": "lc0-shadow"}[arm]
    spec = source["instances"][name]
    options = dict(spec["options"])
    options["MultiPV"] = 1  # standalone role, not the multi-candidate specialist role
    return options, dict(spec.get("environment", {}))


def command(job: dict, directory: Path, p: dict, source: dict, fastchess: Path, *, write_specs: bool = True) -> list[str]:
    argv = [str(fastchess), "-concurrency", "1", "-rounds", "1", "-games", "2", "-repeat",
            "-variant", "standard", "-ratinginterval", "0", "-autosaveinterval", "0",
            "-strict", "-startup-ms", str(p["startup_ms"]), "-ping-ms", str(p["ping_ms"]),
            "-ucinewgame-ms", str(p["ping_ms"]), "-event", job["id"], "-site", "LOCAL-1",
            "-pgnout", f"file={directory / 'games.pgn'}", "notation=san", "append=false",
            "timeleft=true", "pv=false", "-log", f"file={directory / 'fastchess.log'}",
            "level=warn", "append=false"]
    if job["opening"]:
        argv += ["-openings", f"file={ROOT / 'tests/fixtures/local_full_game' / job['opening']}",
                 "format=pgn", "order=sequential"]
    for arm in job["arms"]:
        options, environment = engine_options(arm, source)
        spec_path = directory / f"{arm}.json"
        spec = {"schema_version": 1, "arm": arm, "root": str(ROOT),
                "sessions": str(directory / "sessions" / arm), "environment": environment}
        if write_specs:
            save(spec_path, spec)
        argv += ["-engine", f"name={arm}", f"cmd={sys.executable}",
                 f"args=-m tools.local_game.proxy --spec {spec_path}", f"dir={ROOT}",
                 "proto=uci", f"tc={job['clock']}", f"timemargin={p['timemargin_ms']}",
                 f"restart={'on' if job['restart'] else 'off'}"]
        if job["driver_nodes"] is not None and arm == "stockfish":
            argv += [f"nodes={job['driver_nodes']}"]
        for name, value in options.items():
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            argv += [f"option.{name}={rendered}"]
    return argv


def bounded(argv: list[str], cwd: Path, log: Path, timeout: int) -> dict:
    started = time.monotonic_ns()
    result = {"argv": argv, "started_ns": started, "timed_out": False}
    with log.open("wb") as output:
        process = subprocess.Popen(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            result["returncode"] = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            result["timed_out"] = True
            result["error"] = type(exc).__name__
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=45)  # proxy SIGTERM handlers own descendant cleanup
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            result["returncode"] = process.returncode
        finally:
            result["ended_ns"] = time.monotonic_ns()
    return result


def prerequisites(output: Path) -> list[dict]:
    steps = [
        ("lc0", "scripts/lc0-strength-profile-contract.py", "build/test-results/lc0-strength/report.json"),
        ("online2", "scripts/qualify-online-profile.py", "build/test-results/online-profile/report.json"),
        ("g3", "scripts/qualify-online-hybrid-authority.py", "build/test-results/online-hybrid/report.json"),
    ]
    records = []
    for label, script, report_name in steps:
        # Remove only the well-known old result, so a failed run cannot borrow an old pass.
        report = ROOT / report_name
        report.unlink(missing_ok=True)
        run = bounded([sys.executable, script], ROOT, output / f"{label}.log", 600)
        records.append({"id": label, **run})
        save(output / "prerequisites.json", records)
        require(run["returncode"] == 0 and not run["timed_out"], f"{label} prerequisite failed")
        require(report.is_file(), f"{label}: expected qualification report not produced: {report}")
        shutil.copy2(report, output / f"{label}.json")
    g3 = load(output / "g3.json")["positive_case"]
    require(g3["authority"] == "HYBRID" and g3["emitted_move"] != g3["anchor_move"],
            "G3 prerequisite did not exercise actual non-anchor authority")
    runtime = load(ROOT / "config/allfather.online-hybrid.validation.json")
    source_run = contained(ROOT / runtime["shadow"]["replay_root"], g3["run_id"])
    shutil.copytree(source_run, output / "g3-positive-replay")
    return records


def run(mode: str, output: Path) -> int:
    require(sys.platform == "linux", "LOCAL-1 reference requires Linux procfs")
    require(not any(c.isspace() for c in str(ROOT)), "reference checkout path must have no whitespace")
    require(output.resolve().is_relative_to(ROOT / "build"), "campaign output must be inside build/")
    output.mkdir(parents=True, exist_ok=False)  # never merge a rerun into an earlier campaign
    p = policy()
    source = load(ROOT / p["source_runtime"])
    expected = schedule(p, mode)
    manifest = {"schema_version": 1, "campaign_id": output.name, "mode": mode,
                "source": source_identity(), "status": "running", "planned_jobs": expected,
                "jobs": [], "failures": [], "prerequisites": [],
                "host": {"system": list(os.uname()), "logical_cpus": os.cpu_count()},
                "clock_regime": "same tournament clock; NOT equal aggregate compute",
                "control": "allfather-anchor is one Stockfish process through the legacy native-clock shell"}
    inputs = input_paths(p)
    save(output / "manifest.json", manifest)
    try:
        manifest["inputs"] = [file_record(path) for path in inputs if path.is_file()]
        require(len(manifest["inputs"]) == len(inputs), "missing build/qualification input")
        fastchess = verify_builds(manifest["source"])
        pre = output / "prerequisites"
        pre.mkdir()
        manifest["prerequisites"] = prerequisites(pre)
        from .probes import run_probes
        manifest["rule_probes"] = run_probes(output / "rule-probes", source)
        for job in expected:
            print(f"LOCAL-1 starting {job['id']}: {job['arms']}", flush=True)
            directory = output / job["id"]
            directory.mkdir()
            argv = command(job, directory, p, source, fastchess)
            result = bounded(argv, directory, directory / "runner.log", p["job_timeout_s"])
            entry = {"plan": job, "execution": result}
            manifest["jobs"].append(entry)
            save(output / "manifest.json", manifest)
            if result["returncode"] != 0 or result["timed_out"]:
                manifest["failures"].append(f"{job['id']}: runner failed; retained without retry")
        for record in manifest["inputs"]:
            verify_record(ROOT, record)
        require(source_identity() == manifest["source"], "checkout identity changed during campaign")
        verify_builds(manifest["source"])
        manifest["status"] = "failed" if manifest["failures"] else "completed"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failures"].append(f"{type(exc).__name__}: {exc}")
    finally:
        manifest["artifacts"] = [file_record(path, output) for path in sorted(output.rglob("*"))
                                 if path.is_file() and path not in (output / "manifest.json", output / "report.json")]
        save(output / "manifest.json", manifest)
    from .validate import qualify
    report = qualify(output)
    save(output / "report.json", report)
    print(json_summary(report), flush=True)
    return 0 if report["passed"] else 1


def json_summary(report: dict) -> str:
    import json
    return json.dumps({k: report[k] for k in ("passed", "campaign_id", "errors", "baseline")}, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("required", "soak"), default="required")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "build/test-results/local-full-game" / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    return run(args.mode, output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
