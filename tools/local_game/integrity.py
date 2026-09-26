"""Independent qualification of campaign identities, prerequisites and actual commands."""
from __future__ import annotations

from pathlib import Path
import math
import re

from .common import ROOT, load, require, sha, verify_record, contained, runtime_config


def input_paths(p: dict) -> list[Path]:
    return [ROOT / path for path in (
        "qualification/local-full-game.json", "qualification/fastchess.lock.json",
        "qualification/local-game-requirements.txt", p["source_runtime"],
        "build/online-cpu-reference/build-manifest.json", "build/tools/fastchess/build-manifest.json",
    )] + sorted((ROOT / "tests/fixtures/local_full_game").glob("*"))


def verify_builds(source: dict) -> Path:
    fc_root = ROOT / "build/tools/fastchess"
    fc = load(fc_root / "build-manifest.json")
    lock = ROOT / "qualification/fastchess.lock.json"
    require(fc["source"] == load(lock) and fc["lock_sha256"] == sha(lock), "Fastchess pin mismatch")
    require(fc["upstream_tests_passed"] is True, "Fastchess upstream tests missing")
    fastchess = verify_record(fc_root, fc["binary"])
    verify_record(fc_root, fc["license"])
    bundle = ROOT / "build/online-cpu-reference"
    build = load(bundle / "build-manifest.json")
    require(build["source_commit"] == source["commit"], "ONLINE-2 bundle source mismatch")
    for category in ("engines", "networks"):
        require(set(build["artifacts"][category]) == {"stockfish", "reckless", "lc0"},
                "bundle is missing a constituent identity")
        for item in build["artifacts"][category].values():
            verify_record(bundle, {"path": item["path"], "sha256": item["sha256"], "bytes": item["size"]})
    return fastchess


def verify_prerequisites(output: Path, source: dict) -> None:
    from controller.replay import verify_bundle_integrity
    from controller.final_decision import verify_final_decision_integrity
    from controller.counterfactual import verify_counterfactual_integrity
    root = output / "prerequisites"
    lc0, online, g3 = (load(root / f"{label}.json") for label in ("lc0", "online2", "g3"))
    build = load(ROOT / "build/online-cpu-reference/build-manifest.json")
    require(lc0["commit_sha"] == online["source_commit"] == source["commit"], "stale prerequisite source")
    require(lc0["requested_backend"] == lc0["observed_backend"] == "blas", "real BLAS not observed")
    require(lc0["binary"]["sha256"] == build["artifacts"]["engines"]["lc0"]["sha256"], "LC0 binary mismatch")
    require(lc0["network"]["sha256"] == build["artifacts"]["networks"]["lc0"]["sha256"], "LC0 weights mismatch")
    require(online["contracts"]["build_manifest_sha256"] == sha(ROOT / "build/online-cpu-reference/build-manifest.json"),
            "ONLINE-2 prerequisite bundle mismatch")
    require(online["contracts"]["lc0_reference_report_sha256"] == sha(root / "lc0.json"), "LC0 report binding mismatch")
    positive = g3["positive_case"]
    require(positive["authority"] == "HYBRID" and positive["emitted_move"] != positive["anchor_move"],
            "G3 positive prerequisite is not an actual override")
    run = root / "g3-positive-replay"
    for verifier in (verify_bundle_integrity, verify_final_decision_integrity, verify_counterfactual_integrity):
        problems = verifier(run)
        require(not problems, f"G3 prerequisite replay rejected: {problems}")
    manifest = load(run / "manifest.json")
    final = load(run / "decision/final.json")["decision"]
    require(manifest["run_id"] == positive["run_id"], "G3 prerequisite run identity mismatch")
    require(final["authority"] == "HYBRID" and final["emitted_move"] == positive["emitted_move"] and
            final["anchor_move"] == positive["anchor_move"], "G3 prerequisite report disagrees with played decision")


def verify_probes(output: Path, declared: dict) -> None:
    from .probes import check_transition
    from controller.replay import verify_bundle_integrity
    from controller.final_decision import verify_final_decision_integrity
    root = output / "rule-probes"
    report = load(root / "report.json")
    require(report == declared and report["passed"] is True, "probe report/manifest mismatch")
    cases = load(ROOT / "tests/fixtures/local_full_game/rule-probes.json")
    require([r["case"] for r in report["cases"]] == cases, "missing/extra/modified rule probe")
    used = set()
    for row in report["cases"]:
        board = check_transition(row["case"], row["emitted"])
        require(row["after"] == board.fen(en_passant="fen"), "false post-transition FEN")
        require(row["run_id"] not in used, "reused probe replay")
        used.add(row["run_id"])
        run = contained(root / "replays", row["run_id"])
        for verifier in (verify_bundle_integrity, verify_final_decision_integrity):
            problems = verifier(run)
            require(not problems, f"rule-probe replay rejected: {problems}")
        m = load(run / "manifest.json")
        final = load(run / "decision/final.json")["decision"]
        require(final["emitted_move"] == row["emitted"], "probe final decision mismatch")
        require(m["position"]["base_fen"] == row["case"]["fen"] and
                m["position"]["moves"] == row["case"]["moves"], "probe history mismatch")
        require(m["clock_outcome"]["output_within_deadline"] is True, "late probe move")
    require({p.name for p in (root / "replays").iterdir() if p.is_dir()} == used, "orphan probe replay")


def verify_session_commands(events: list[dict], arm: str, plan: dict, source: dict) -> None:
    """Check actual transmitted UCI limits/options, not just the runner's desired argv."""
    from .runner import engine_options
    required, _ = engine_options(arm, source)
    options = {}
    _, increment = plan["clock"].split("+")
    expected_increment = int(float(increment) * 1000)
    for event in events:
        if event["direction"] != "in":
            continue
        line = event["line"]
        if line.startswith("setoption "):
            match = re.fullmatch(r"setoption name (.*?)(?: value(?: (.*))?)?", line)
            require(match is not None, "malformed recorded option")
            options[match[1]] = match[2] or ""
            require(not (match[1] == "Ponder" and match[2] == "true"), "ponder enabled")
        if line == "go" or line.startswith("go "):
            for name, value in required.items():
                rendered = str(value).lower() if isinstance(value, bool) else str(value)
                require(options.get(name) == rendered, f"{arm}: effective {name} differs from baseline policy")
            words = line.split()[1:]
            require(len(words) % 2 == 0, "unexpected search restriction/limit in tournament")
            limits = dict(zip(words[::2], words[1::2]))
            require(len(limits) * 2 == len(words), "duplicate UCI limit")
            allowed = {"wtime", "btime", "winc", "binc"}
            if plan["driver_nodes"] is not None and arm == "stockfish":
                allowed.add("nodes")
                require(limits.get("nodes") == str(plan["driver_nodes"]), "driver work limit drift")
            require(set(limits) == allowed, "baseline received non-clock or missing limits")
            require(int(limits["wtime"]) >= 0 and int(limits["btime"]) >= 0, "negative tournament clock")
            require(int(limits["winc"]) == int(limits["binc"]) == expected_increment, "increment differs across arms")


def finite_metrics(summary: dict) -> None:
    for metric in summary["search_metrics"]:
        value = metric["cpu_ms_observed"]
        require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0),
                "invalid CPU metric")
    for key in ("reaped_subtree_cpu_ms", "proxy_cpu_ms"):
        value = summary["resources"][key]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0, "invalid session CPU")
