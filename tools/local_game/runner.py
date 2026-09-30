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

from .common import (ARMS, ROOT, contained, file_record, load, policy, require,
                     safe_copy_regular_tree, save, sha, source_identity,
                     terminate_token_processes, verify_record)
from .integrity import input_paths, verify_builds
from tools.engine_opt.domain import candidate_bundle_identity, load_execution_domain


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
    # Fastchess's engine CLI rejects key=value pairs whose value is empty.
    # An omitted empty option means "leave the engine's declared default in
    # force"; LOCAL-1 binds that omission through the frozen source profile and
    # still validates every option actually transmitted.
    options = {name: value for name, value in options.items() if value != ""}
    return options, dict(spec.get("environment", {}))


def command(job: dict, directory: Path, p: dict, source: dict, fastchess: Path, *, write_specs: bool = True) -> list[str]:
    argv = [str(fastchess), "-concurrency", "1", "-rounds", "1", "-games", "2", "-repeat",
            "-variant", "standard", "-ratinginterval", "0", "-autosaveinterval", "0",
            "-startup-ms", str(p["startup_ms"]), "-ping-ms", str(p["ping_ms"]),
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
                "sessions": str(directory / "sessions" / arm), "environment": environment,
                "source_runtime": p["source_runtime"]}
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


def bounded(argv: list[str], cwd: Path, log: Path, timeout: int, *, extra_environment: dict[str, str] | None = None) -> dict:
    """Run one bounded command and clean every live descendant it spawned.

    Ownership is inherited through an environment token rather than inferred
    from a reusable PID/PGID or from evidence files that might fail to persist.
    Any live child observed after the bounded parent exits is permanent failure
    evidence even when emergency cleanup succeeds.
    """
    started = time.monotonic_ns()
    token = uuid.uuid4().hex
    environment = os.environ.copy()
    if extra_environment:
        environment.update(extra_environment)
    environment["ALLFATHER_LOCAL1_PROCESS_TOKEN"] = token
    result = {
        "argv": argv,
        "started_ns": started,
        "timed_out": False,
        "process_token": token,
    }
    with log.open("wb") as output:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=environment,
        )
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
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            result["returncode"] = process.returncode
        finally:
            before_cleanup, after_cleanup = terminate_token_processes(token)
            result["descendants_before_cleanup"] = before_cleanup
            result["descendants_after_cleanup"] = after_cleanup
            result["ended_ns"] = time.monotonic_ns()
    return result


def referenced_run_ids(report: dict, label: str) -> list[str]:
    rows = report.get("cases")
    require(isinstance(rows, list) and rows, f"{label}: report has no cases")
    ids = [row.get("run_id") for row in rows]
    require(all(isinstance(run_id, str) and run_id for run_id in ids),
            f"{label}: report contains missing run_id")
    require(len(ids) == len(set(ids)), f"{label}: duplicate run_id in report")
    return ids


def retain_report_runs(source_root: Path, destination: Path, run_ids: list[str],
                       prior: set[str], label: str) -> None:
    require(source_root.is_dir(), f"{label}: replay root missing")
    current = {
        path.name for path in source_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    require(set(run_ids) <= (current - prior),
            f"{label}: report referenced a replay that was not freshly created")
    destination.mkdir()
    for run_id in run_ids:
        raw_source = source_root / run_id
        require(raw_source.is_dir() and not raw_source.is_symlink(),
                f"{label}: top-level replay run is not a real directory: {run_id}")
        source = contained(source_root, run_id)
        safe_copy_regular_tree(source, destination / run_id)


def prerequisites(output: Path, p: dict, execution_domain_path: Path | None = None) -> list[dict]:
    """Run the prerequisite family declared by the selected lifecycle policy."""
    declared = p.get("prerequisites")
    if declared is not None:
        require(isinstance(declared, list) and declared, "declared prerequisites must be non-empty")
        records=[]
        for row in declared:
            require(isinstance(row, dict), "malformed prerequisite declaration")
            label, script, report_name = row.get("id"), row.get("script"), row.get("report")
            require(all(isinstance(x, str) and x for x in (label, script, report_name)),
                    "prerequisite id/script/report must be non-empty strings")
            retain = row.get("retain_case_replays", False)
            require(isinstance(retain, bool), f"{label}: retain_case_replays must be boolean")
            replay_root = None
            replay_before: set[str] = set()
            if retain:
                replay_value = row.get("replay_root")
                require(isinstance(replay_value, str) and replay_value,
                        f"{label}: retained prerequisite requires replay_root")
                replay_root = ROOT / replay_value
                replay_before = (
                    {path.name for path in replay_root.iterdir() if path.is_dir()}
                    if replay_root.is_dir() else set()
                )
            report=ROOT / report_name
            report.unlink(missing_ok=True)
            argv=[sys.executable, script]
            if label == "g3-v2":
                argv.append("--record-disposition")
            domain_env = (
                {"ALLFATHER_EXECUTION_DOMAIN_PATH": str(execution_domain_path)}
                if execution_domain_path is not None else None
            )
            result=bounded(
                argv,
                ROOT,
                output / f"{label}.log",
                1200,
                extra_environment=domain_env,
            )
            records.append({"id": label, **result})
            save(output / "prerequisites.json", records)
            require(result["returncode"] == 0 and not result["timed_out"], f"{label} prerequisite failed")
            require(report.is_file(), f"{label}: expected prerequisite report not produced: {report}")
            shutil.copy2(report, output / f"{label}.json")
            if retain:
                report_doc = load(report)
                run_ids = referenced_run_ids(report_doc, label)
                require(replay_root is not None, f"{label}: internal replay-root error")
                retain_report_runs(
                    replay_root,
                    output / f"{label}-replays",
                    run_ids,
                    replay_before,
                    label,
                )
        return records

    online_runtime = load(ROOT / "config/allfather.online.cpu-reference.json")
    g3_runtime = load(ROOT / "config/allfather.online-hybrid.validation.json")
    replay_roots = {
        "online2": ROOT / online_runtime["shadow"]["replay_root"],
        "g3": ROOT / g3_runtime["shadow"]["replay_root"],
    }
    replay_snapshots = {
        label: ({path.name for path in root.iterdir() if path.is_dir()} if root.is_dir() else set())
        for label, root in replay_roots.items()
    }
    steps = [
        ("lc0", "scripts/lc0-strength-profile-contract.py", "build/test-results/lc0-strength/report.json"),
        ("online2", "scripts/qualify-online-profile.py", "build/test-results/online-profile/report.json"),
        ("g3", "scripts/qualify-online-hybrid-authority.py", "build/test-results/online-hybrid/report.json"),
    ]
    records=[]
    for label, script, report_name in steps:
        report=ROOT / report_name
        report.unlink(missing_ok=True)
        argv=[sys.executable, script]
        if label == "g3":
            argv.append("--local1-prerequisite")
        result=bounded(argv, ROOT, output / f"{label}.log", 600)
        records.append({"id": label, **result})
        save(output / "prerequisites.json", records)
        require(result["returncode"] == 0 and not result["timed_out"], f"{label} prerequisite failed")
        require(report.is_file(), f"{label}: expected qualification report not produced: {report}")
        shutil.copy2(report, output / f"{label}.json")
    online=load(output / "online2.json")
    g3_report=load(output / "g3.json")
    g3=g3_report["positive_case"]
    require(g3["authority"] == "HYBRID" and g3["emitted_move"] != g3["anchor_move"],
            "G3 prerequisite did not exercise actual non-anchor authority")
    online_ids=referenced_run_ids(online, "ONLINE-2")
    g3_ids=referenced_run_ids(g3_report, "G3")
    retain_report_runs(replay_roots["online2"], output / "online2-replays", online_ids,
                       replay_snapshots["online2"], "ONLINE-2")
    retain_report_runs(replay_roots["g3"], output / "g3-replays", g3_ids,
                       replay_snapshots["g3"], "G3")
    require(g3["run_id"] in set(g3_ids), "positive G3 run is absent from current report")
    return records

def run(mode: str, output: Path, *, shard_index: int = 0, shard_count: int = 1,
        policy_path: Path | None = None, execution_domain_path: Path | None = None) -> int:
    require(sys.platform == "linux", "LOCAL-1 reference requires Linux procfs")
    require(not any(c.isspace() for c in str(ROOT)), "reference checkout path must have no whitespace")
    require(output.resolve().is_relative_to(ROOT / "build"), "campaign output must be inside build/")
    output.mkdir(parents=True, exist_ok=False)  # never merge a rerun into an earlier campaign
    policy_file = (ROOT / "qualification/local-full-game.json" if policy_path is None
                   else policy_path if policy_path.is_absolute() else ROOT / policy_path).resolve()
    p = policy(path=policy_file)
    source = load(ROOT / p["source_runtime"])
    source_id = source_identity()
    execution_domain = None
    candidate_bundle = None
    if p.get("profile_id") == "local-full-game-v2":
        require(execution_domain_path is not None,
                "LOCAL-1-v2 requires canonical execution-domain evidence")
        execution_domain_path = execution_domain_path.resolve()
        require(execution_domain_path.is_file(),
                "LOCAL-1-v2 execution-domain file is missing")
        execution_domain = load_execution_domain(
            execution_domain_path,
            expected_source_commit=source_id["commit"],
        )
        candidate_bundle = candidate_bundle_identity(
            ROOT / p["bundle_root"],
            expected_source_commit=source_id["commit"],
        )
    full_schedule = schedule(p, mode)
    require(type(shard_index) is int and type(shard_count) is int and
            shard_count >= 1 and 0 <= shard_index < shard_count,
            "invalid campaign shard")
    if mode == "soak" and shard_count > 1:
        expected = [job for index, job in enumerate(full_schedule)
                    if index % shard_count == shard_index]
    else:
        require(shard_count == 1 and shard_index == 0,
                "sharding is supported only for soak mode")
        expected = full_schedule
    manifest = {"schema_version": 1, "campaign_id": output.name, "mode": mode,
                "shard": {"index": shard_index, "count": shard_count},
                "source": source_id, "policy": file_record(policy_file),
                "status": "running", "planned_jobs": expected,
                "jobs": [], "failures": [], "prerequisites": [],
                "host": {"system": list(os.uname()), "logical_cpus": os.cpu_count()},
                "clock_regime": "same tournament clock; NOT equal aggregate compute",
                "control": "allfather-anchor is one Stockfish process through the legacy native-clock shell"}
    if execution_domain is not None:
        manifest["execution_domain"] = execution_domain
        manifest["candidate_bundle"] = candidate_bundle
    inputs = input_paths(p, policy_file)
    save(output / "manifest.json", manifest)
    try:
        manifest["inputs"] = [file_record(path) for path in inputs if path.is_file()]
        require(len(manifest["inputs"]) == len(inputs), "missing build/qualification input")
        fastchess = verify_builds(manifest["source"], p)
        pre = output / "prerequisites"
        pre.mkdir()
        manifest["prerequisites"] = prerequisites(pre, p, execution_domain_path)
        from .probes import run_probes
        manifest["rule_probes"] = run_probes(output / "rule-probes", source)
        from .faults import run_faults
        manifest["fault_cases"] = run_faults(output, p)
        save(output / "manifest.json", manifest)
        for job in expected:
            print(f"LOCAL-1 starting {job['id']}: {job['arms']}", flush=True)
            directory = output / job["id"]
            directory.mkdir()
            argv = command(job, directory, p, source, fastchess)
            result = bounded(argv, directory, directory / "runner.log", p["job_timeout_s"])
            entry = {"plan": job, "execution": result}
            manifest["jobs"].append(entry)
            save(output / "manifest.json", manifest)
            if (result["returncode"] != 0 or result["timed_out"]
                    or result.get("descendants_before_cleanup")
                    or result.get("descendants_after_cleanup")):
                manifest["failures"].append(
                    f"{job['id']}: runner/lifecycle cleanup failed; retained without retry"
                )
        for record in manifest["inputs"]:
            verify_record(ROOT, record)
        require(source_identity() == manifest["source"], "checkout identity changed during campaign")
        verify_builds(manifest["source"], p)
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
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--execution-domain", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "build/test-results/local-full-game" / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    return run(args.mode, output.resolve(),
               shard_index=args.shard_index, shard_count=args.shard_count,
               policy_path=args.policy,
               execution_domain_path=args.execution_domain)


if __name__ == "__main__":
    raise SystemExit(main())
