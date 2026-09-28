"""Independent qualification of campaign identities, prerequisites and actual commands."""
from __future__ import annotations

from pathlib import Path
import math
import re
import subprocess

from .common import ROOT, load, require, sha, verify_record, contained, runtime_config


def input_paths(p: dict, policy_path: Path | None = None) -> list[Path]:
    fixtures = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "tests/fixtures/local_full_game"],
        text=True,
    ).splitlines()
    require(fixtures, "no committed LOCAL-1 fixtures found")
    selected_policy = ROOT / "qualification/local-full-game.json" if policy_path is None else Path(policy_path)
    bundle_root = p.get("bundle_root", "build/online-cpu-reference")
    return [selected_policy, ROOT / "qualification/fastchess.lock.json",
            ROOT / "qualification/local-game-requirements.txt", ROOT / p["source_runtime"],
            ROOT / bundle_root / "build-manifest.json", ROOT / "build/tools/fastchess/build-manifest.json"] + [ROOT / path for path in fixtures]


def verify_fastchess_attestation(fc_root: Path, fc: dict, lock_path: Path) -> Path:
    source_lock = load(lock_path)
    attestation = fc.get("source_test_attestation")
    require(isinstance(attestation, dict),
            "Fastchess source-test attestation record is mandatory")
    attestation_path = verify_record(fc_root, attestation)
    require(not attestation_path.is_symlink(),
            "Fastchess source-test attestation may not be a symlink")
    tested = load(attestation_path)
    require(fc.get("upstream_tests_passed") is True,
            "Fastchess build does not declare successful source tests")
    require(
        tested.get("passed") is True
        and tested.get("repository") == source_lock["repository"]
        and tested.get("commit") == source_lock["commit"]
        and tested.get("tree") == source_lock["tree"]
        and tested.get("lock_sha256") == fc["lock_sha256"]
        and tested.get("reference_host") == "ubuntu-22.04"
        and (tested.get("os_release") or {}).get("ID") == "ubuntu"
        and (tested.get("os_release") or {}).get("VERSION_ID") == "22.04"
        and tested.get("contract") == "clean -> make tests -> fastchess-tests"
        and isinstance(tested.get("compiler"), str)
        and bool(tested["compiler"].strip()),
        "Fastchess source-test attestation host/source contract mismatch",
    )
    return attestation_path


def verify_builds(source: dict, p: dict | None = None) -> Path:
    fc_root = ROOT / "build/tools/fastchess"
    fc = load(fc_root / "build-manifest.json")
    lock = ROOT / "qualification/fastchess.lock.json"
    require(fc["source"] == load(lock) and fc["lock_sha256"] == sha(lock), "Fastchess pin mismatch")
    fastchess = verify_record(fc_root, fc["binary"])
    verify_record(fc_root, fc["license"])
    verify_fastchess_attestation(fc_root, fc, lock)

    bundle = ROOT / ((p or {}).get("bundle_root", "build/online-cpu-reference"))
    build = load(bundle / "build-manifest.json")
    require(build["source_commit"] == source["commit"], "ONLINE-2 bundle source mismatch")
    for category in ("engines", "networks"):
        require(set(build["artifacts"][category]) == {"stockfish", "reckless", "lc0"},
                "bundle is missing a constituent identity")
        for item in build["artifacts"][category].values():
            verify_record(bundle, {"path": item["path"], "sha256": item["sha256"], "bytes": item["size"]})
    return fastchess


def verify_prerequisites(output: Path, source: dict, p: dict | None = None) -> None:
    from controller.replay import verify_bundle_integrity
    from controller.final_decision import verify_final_decision_integrity
    from controller.counterfactual import verify_counterfactual_integrity

    root = output / "prerequisites"
    if p is not None and p.get("prerequisites") is not None:
        declared = p["prerequisites"]
        retained = load(root / "prerequisites.json")
        require([row.get("id") for row in retained] == [row.get("id") for row in declared],
                "declared prerequisite execution differs from policy")
        reports = {}
        for row in declared:
            label = row["id"]
            report = load(root / f"{label}.json")
            require(report.get("passed") is True, f"{label}: prerequisite report did not pass")
            reports[label] = report
            if row.get("retain_case_replays", False):
                run_ids = [case.get("run_id") for case in report.get("cases", [])]
                require(run_ids and all(isinstance(run_id, str) and run_id for run_id in run_ids),
                        f"{label}: retained prerequisite report has missing run ids")
                require(len(run_ids) == len(set(run_ids)),
                        f"{label}: retained prerequisite report reuses a run id")
                replay_root = root / f"{label}-replays"
                require(replay_root.is_dir(), f"{label}: retained replay evidence missing")
                actual = {path.name for path in replay_root.iterdir() if path.is_dir()}
                require(actual == set(run_ids),
                        f"{label}: retained replay set differs from report")
                for run_id in run_ids:
                    run = contained(replay_root, run_id)
                    problems = verify_bundle_integrity(run)
                    require(not problems, f"{label}: replay integrity failed for {run_id}: {problems}")
                    problems = verify_final_decision_integrity(run)
                    require(not problems, f"{label}: final decision integrity failed for {run_id}: {problems}")
                    if (run / "decision/counterfactual.json").is_file():
                        problems = verify_counterfactual_integrity(run)
                        require(not problems, f"{label}: counterfactual integrity failed for {run_id}: {problems}")
                positive = report.get("positive_case")
                if isinstance(positive, dict):
                    require(positive.get("run_id") in set(run_ids),
                            f"{label}: positive run is not retained")
                    run = contained(replay_root, positive["run_id"])
                    final = load(run / "decision/final.json")["decision"]
                    require(
                        final.get("authority") == positive.get("authority")
                        and final.get("emitted_move") == positive.get("emitted_move")
                        and final.get("anchor_move") == positive.get("anchor_move"),
                        f"{label}: retained positive decision disagrees with report",
                    )
        engine = reports.get("engine-opt-v2")
        require(isinstance(engine, dict) and engine.get("promotion_ready") is True,
                "ENGINE-OPT-V2 selection is not measured/frozen for promotion")
        return
    lc0, online, g3 = (load(root / f"{label}.json") for label in ("lc0", "online2", "g3"))
    build = load(ROOT / "build/online-cpu-reference/build-manifest.json")
    require(lc0["commit_sha"] == online["source_commit"] == source["commit"], "stale prerequisite source")
    require(lc0["requested_backend"] == lc0["observed_backend"] == "blas", "real BLAS not observed")
    require(lc0["binary"]["sha256"] == build["artifacts"]["engines"]["lc0"]["sha256"], "LC0 binary mismatch")
    require(lc0["network"]["sha256"] == build["artifacts"]["networks"]["lc0"]["sha256"], "LC0 weights mismatch")
    require(online["contracts"]["build_manifest_sha256"] == sha(ROOT / "build/online-cpu-reference/build-manifest.json"),
            "ONLINE-2 prerequisite bundle mismatch")
    require(online["contracts"]["lc0_reference_report_sha256"] == sha(root / "lc0.json"), "LC0 report binding mismatch")
    online_replays = root / "online2-replays"
    require(online_replays.is_dir(), "ONLINE-2 prerequisite replay evidence missing")
    online_run_ids = [row["run_id"] for row in online.get("cases", [])]
    require(online_run_ids and len(online_run_ids) == len(set(online_run_ids)),
            "ONLINE-2 prerequisite run identities missing/duplicated")
    require(
        {path.name for path in online_replays.iterdir() if path.is_dir()} == set(online_run_ids),
        "ONLINE-2 prerequisite replay root contains missing/orphan runs",
    )
    for run_id in online_run_ids:
        run_path = contained(online_replays, run_id)
        require(run_path.is_dir(), f"ONLINE-2 replay missing: {run_id}")
        problems = verify_bundle_integrity(run_path)
        require(not problems, f"ONLINE-2 prerequisite replay rejected: {problems}")
        require(load(run_path / "manifest.json").get("run_id") == run_id,
                "ONLINE-2 replay directory/run_id mismatch")

    g3_replays = root / "g3-replays"
    require(g3_replays.is_dir(), "G3 prerequisite replay root missing")
    g3_run_ids = [row["run_id"] for row in g3.get("cases", [])]
    require(g3_run_ids and len(g3_run_ids) == len(set(g3_run_ids)),
            "G3 prerequisite run identities missing/duplicated")
    require(
        {path.name for path in g3_replays.iterdir() if path.is_dir()} == set(g3_run_ids),
        "G3 prerequisite replay root contains missing/orphan runs",
    )
    for run_id in g3_run_ids:
        run_path = contained(g3_replays, run_id)
        require(load(run_path / "manifest.json").get("run_id") == run_id,
                "G3 prerequisite replay directory/run_id mismatch")
        problems = verify_bundle_integrity(run_path)
        require(not problems, f"G3 prerequisite replay rejected: {problems}")
        problems = verify_final_decision_integrity(run_path)
        require(not problems, f"G3 prerequisite final decision rejected: {problems}")
        if (run_path / "decision/counterfactual.json").is_file():
            problems = verify_counterfactual_integrity(run_path)
            require(not problems, f"G3 prerequisite counterfactual rejected: {problems}")

    positive = g3["positive_case"]
    require(positive["authority"] == "HYBRID" and positive["emitted_move"] != positive["anchor_move"],
            "G3 positive prerequisite is not an actual override")
    run = contained(g3_replays, positive["run_id"])
    require(run.is_dir(), "G3 prerequisite positive run missing")
    for verifier in (verify_bundle_integrity, verify_final_decision_integrity,
                     verify_counterfactual_integrity):
        problems = verifier(run)
        require(not problems, f"G3 prerequisite replay rejected: {problems}")
    manifest = load(run / "manifest.json")
    final = load(run / "decision/final.json")["decision"]
    require(manifest["run_id"] == positive["run_id"],
            "G3 prerequisite run identity mismatch")
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
        require(m["run_id"] == run.name, "probe replay directory/run_id mismatch")
        final = load(run / "decision/final.json")["decision"]
        require(final["emitted_move"] == row["emitted"], "probe final decision mismatch")
        require(m["position"]["base_fen"] == row["case"]["fen"] and
                m["position"]["moves"] == row["case"]["moves"], "probe history mismatch")
        require(m["clock_outcome"]["output_within_deadline"] is True, "late probe move")
    require({p.name for p in (root / "replays").iterdir() if p.is_dir()} == used, "orphan probe replay")


def _clock_ms(control: str) -> tuple[int, int]:
    require(isinstance(control, str) and "+" in control, "clock must contain an increment")
    base, increment = control.rsplit("+", 1)

    def seconds(text: str) -> float:
        parts = text.split(":")
        require(1 <= len(parts) <= 3 and all(part != "" for part in parts), "malformed base clock")
        total = 0.0
        for index, part in enumerate(reversed(parts)):
            value = float(part)
            require(math.isfinite(value) and value >= 0, "invalid clock component")
            total += value * (60 ** index)
        return total

    base_ms = int(round(seconds(base) * 1000))
    inc_value = float(increment)
    require(math.isfinite(inc_value) and inc_value >= 0, "invalid increment")
    return base_ms, int(round(inc_value * 1000))


def parse_go_limits(command: str) -> dict[str, str]:
    words = command.split()
    require(words and words[0] == "go", "malformed UCI go command")
    payload = words[1:]
    require(len(payload) % 2 == 0, "unexpected search restriction/limit in tournament")
    pairs = list(zip(payload[::2], payload[1::2]))
    limits = dict(pairs)
    require(len(limits) == len(pairs), "duplicate UCI limit")
    return limits


def verify_session_commands(events: list[dict], arm: str, plan: dict, source: dict) -> None:
    """Check actual transmitted UCI limits/options and the scheduled clock."""
    from .runner import engine_options

    required, _ = engine_options(arm, source)
    options = {}
    _, expected_increment = _clock_ms(plan["clock"])
    searches = 0

    for event in events:
        if event["direction"] != "in":
            continue
        line = event["line"]
        if line.startswith("setoption "):
            match = re.fullmatch(r"setoption name (.*?)(?: value(?: (.*))?)?", line)
            require(match is not None, "malformed recorded option")
            options[match[1]] = match[2] or ""
            require(not (match[1] == "Ponder" and (match[2] or "").lower() == "true"),
                    "ponder enabled")
        if line == "go" or line.startswith("go "):
            searches += 1
            for name, value in required.items():
                rendered = str(value).lower() if isinstance(value, bool) else str(value)
                require(options.get(name) == rendered,
                        f"{arm}: effective {name} differs from baseline policy")
            limits = parse_go_limits(line)
            allowed = {"wtime", "btime", "winc", "binc"}
            if plan["driver_nodes"] is not None and arm == "stockfish":
                allowed.add("nodes")
                require(limits.get("nodes") == str(plan["driver_nodes"]),
                        "driver work limit drift")
            require(set(limits) == allowed, "baseline received non-clock or missing limits")
            game = event.get("game")
            require(type(game) is int and game >= 1,
                    "go is not bound to a positive game ordinal")
            wtime, btime = int(limits["wtime"]), int(limits["btime"])
            winc, binc = int(limits["winc"]), int(limits["binc"])
            require(wtime >= 0 and btime >= 0, "negative tournament clock")
            require(winc == binc == expected_increment, "increment differs across arms")

    require(searches > 0, f"{arm}: session transmitted no go command")


def _number(value, label: str) -> float:
    require(type(value) in (int, float) and math.isfinite(float(value)) and float(value) >= 0,
            f"{label} must be finite and non-negative")
    return float(value)


def _close(actual, expected, label: str, tolerance: float = 0.02) -> None:
    require(abs(_number(actual, label) - float(expected)) <= tolerance,
            f"{label} arithmetic mismatch: {actual!r} vs {expected!r}")


def verify_specialist_settlements(actions: list, open_reservations: int) -> bool:
    require(type(open_reservations) is int and open_reservations >= 0, "budget open_reservations must be a non-negative integer")
    grants: dict[str, dict] = {}
    resolutions: dict[str, dict] = {}
    for action in actions:
        require(isinstance(action, dict), "malformed specialist action")
        event = action.get("event")
        token = action.get("reservation_token")
        require(event in ("authorize", "settle", "release"), f"unknown specialist reservation event: {event!r}")
        require(type(action.get("granted")) is bool, "specialist action granted flag must be boolean")
        require(isinstance(action.get("phase"), str) and action["phase"], "specialist action phase is missing")
        _number(action.get("requested_cpu_ms"), "specialist requested_cpu_ms")
        _number(action.get("requested_gpu_ms"), "specialist requested_gpu_ms")
        if event == "authorize":
            if action["granted"]:
                require(isinstance(token, str) and token, "granted authorization is missing reservation token")
                require(token not in grants, "reservation token authorized twice")
                grants[token] = action
            else:
                require(token is None, "denied authorization may not carry a reservation token")
        else:
            require(action["granted"] is True and isinstance(token, str) and token, "specialist resolution is missing granted reservation token")
            require(token in grants, "specialist resolution has no prior authorization")
            require(token not in resolutions, "reservation token settled/released more than once")
            grant = grants[token]
            require(action.get("phase") == grant.get("phase"), "specialist resolution phase differs from authorization")
            require(action.get("requested_cpu_ms") == grant.get("requested_cpu_ms") and action.get("requested_gpu_ms") == grant.get("requested_gpu_ms"), "specialist resolution resource request differs from authorization")
            resolutions[token] = action
    return open_reservations == 0 and set(grants) == set(resolutions)

def verify_resource_claim(run: Path, manifest: dict) -> dict:
    """Reconstruct physical/resource-envelope qualification from primitive rows.

    Producer booleans are compared with independently derived values; they are
    never accepted as the premise of the LOCAL-1 gate.
    """
    from common.search_request import parse_go_request
    from controller.budget import ResourceEnvelope

    resource_path = run / "resource.json"
    route_path = run / "route.json"
    resource, route = load(resource_path), load(route_path)
    require(resource.get("run_id") == manifest.get("run_id") == run.name,
            "resource/manifest/directory run identity mismatch")

    settings = resource.get("settings") or {}
    require(settings.get("enabled") is True, "resource measurement disabled")
    require(resource.get("provider") == settings.get("provider") == "linux-procfs-v1",
            "unexpected resource provider")
    require(resource.get("provider_error") is None and resource.get("interval_error") is None,
            "resource provider/interval error")

    stages = resource.get("stages")
    processes = resource.get("processes")
    require(isinstance(stages, list) and stages, "resource stages are missing")
    require(isinstance(processes, dict) and processes, "resource process totals are missing")

    stage_cpu = 0.0
    stage_complete = True
    for row in stages:
        require(isinstance(row, dict) and type(row.get("complete")) is bool,
                "malformed measured stage")
        if row["complete"]:
            stage_cpu += _number(row.get("cpu_ms"), "stage cpu_ms")
            _number(row.get("wall_ms"), "stage wall_ms")
        else:
            stage_complete = False
            require(row.get("cpu_ms") is None, "incomplete stage carries fabricated CPU")

    process_cpu = 0.0
    process_complete = True
    for name, row in processes.items():
        require(isinstance(name, str) and name and isinstance(row, dict) and
                type(row.get("complete")) is bool, "malformed process total")
        if row["complete"]:
            process_cpu += _number(row.get("cpu_ms"), f"{name} cpu_ms")
        else:
            process_complete = False
            require(row.get("cpu_ms") is None, f"incomplete process {name} carries fabricated CPU")

    controller = resource.get("controller") or {}
    controller_cpu = _number(controller.get("cpu_ms"), "controller cpu_ms")
    _close(resource.get("stage_engine_cpu_ms"), round(stage_cpu, 3), "stage_engine_cpu_ms")
    _close(resource.get("engine_cpu_ms"), round(process_cpu, 3), "engine_cpu_ms")
    physical = process_cpu + controller_cpu
    _close(resource.get("physical_cpu_ms"), round(physical, 3), "physical_cpu_ms")

    cpu_complete = bool(
        resource.get("provider_error") is None
        and resource.get("interval_error") is None
        and stage_complete
        and process_complete
    )
    gpu_complete = False
    coverage = resource.get("coverage") or {}
    cpu = coverage.get("cpu") or {}
    gpu = coverage.get("gpu") or {}
    require(cpu.get("required") is settings.get("require_cpu_for_claim"),
            "CPU-required coverage flag differs from settings")
    require(gpu.get("required") is settings.get("require_gpu_for_claim"),
            "GPU-required coverage flag differs from settings")
    require(cpu.get("complete") is cpu_complete, "CPU coverage flag contradicts primitive rows")
    require(gpu.get("complete") is gpu_complete, "GPU coverage flag contradicts provider semantics")

    expected_qualified = bool(
        settings.get("enabled")
        and (cpu_complete if settings.get("require_cpu_for_claim") else True)
        and (gpu_complete if settings.get("require_gpu_for_claim") else True)
    )
    require(resource.get("qualified") is expected_qualified,
            "resource qualified flag contradicts reconstructed coverage")

    route_resource = route.get("resource_measurement") or {}
    require(route_resource.get("path") == "resource.json", "route points at wrong resource artifact")
    require(route_resource.get("sha256") == sha(resource_path), "route does not hash-bind resource.json")
    for key in ("report_id", "provider", "qualified", "physical_cpu_ms", "engine_cpu_ms", "controller_cpu_ms"):
        expected = resource.get(key) if key != "controller_cpu_ms" else controller.get("cpu_ms")
        require(route_resource.get(key) == expected, f"route/resource {key} mismatch")

    time_plan = manifest.get("time_plan") or {}
    route_plan = route.get("time_plan") or {}
    require(route_plan.get("plan_id") == time_plan.get("plan_id"), "route/manifest TimePlan mismatch")
    envelope_doc = route.get("envelope") or {}
    budget = route.get("budget") or {}
    require(budget.get("envelope") == envelope_doc, "budget/envelope identity mismatch")
    envelope = ResourceEnvelope.from_config(envelope_doc)

    lanes = budget.get("lanes") or {}
    require(isinstance(lanes, dict) and lanes, "budget lane rows missing")
    lane_cpu = lane_gpu = 0.0
    for lane, row in lanes.items():
        require(isinstance(lane, str) and lane and isinstance(row, dict),
                "malformed budget lane")
        reserved_cpu = _number(row.get("reserved_cpu_ms"), f"{lane} reserved cpu")
        spent_cpu = _number(row.get("spent_cpu_ms"), f"{lane} spent cpu")
        reserved_gpu = _number(row.get("reserved_gpu_ms"), f"{lane} reserved gpu")
        spent_gpu = _number(row.get("spent_gpu_ms"), f"{lane} spent gpu")
        lane_cpu += reserved_cpu + spent_cpu
        lane_gpu += reserved_gpu + spent_gpu
        settlements = row.get("settlements")
        require(type(settlements) is int and settlements >= 0,
                f"{lane} settlements must be non-negative integer")
        source_settlements = sum(
            int(row.get(key, 0))
            for key in (
                "measured_settlements",
                "estimated_settlements",
                "declared_fallback_settlements",
            )
        )
        require(source_settlements == settlements,
                f"{lane} settlement provenance count mismatch")

    purpose = budget.get("purpose_totals") or {}
    require(set(purpose) >= {"solver", "verify", "refine", "controller"}, "budget purpose totals missing")
    committed_cpu = 0.0
    committed_gpu = 0.0
    committed_by_purpose = {}
    for name in ("solver", "verify", "refine", "controller"):
        row = purpose[name]
        cpu_value = _number(row.get("reserved_cpu_ms"), f"{name} reserved cpu") + _number(
            row.get("spent_cpu_ms"), f"{name} spent cpu")
        gpu_value = _number(row.get("reserved_gpu_ms"), f"{name} reserved gpu") + _number(
            row.get("spent_gpu_ms"), f"{name} spent gpu")
        committed_by_purpose[name] = (cpu_value, gpu_value)
        committed_cpu += cpu_value
        committed_gpu += gpu_value

    _close(lane_cpu, committed_cpu, "lane/purpose committed CPU")
    _close(lane_gpu, committed_gpu, "lane/purpose committed GPU")
    _close(budget.get("committed_cpu_ms"), round(committed_cpu, 3), "budget committed_cpu_ms")
    _close(budget.get("committed_gpu_ms"), round(committed_gpu, 3), "budget committed_gpu_ms")
    within_envelope = committed_cpu <= envelope.cpu_ms + 1e-6 and committed_gpu <= envelope.gpu_ms + 1e-6
    require(budget.get("within_envelope") is within_envelope, "budget within_envelope flag is not derived")

    caps = {
        "solver": (envelope.solver_cpu_ceiling_ms, envelope.solver_gpu_ceiling_ms),
        "verify": (envelope.verification_reserve_ms, envelope.verification_gpu_reserve_ms),
        "refine": (envelope.refinement_reserve_ms, envelope.refinement_gpu_reserve_ms),
        "controller": (envelope.controller_overhead_reserve_ms, 0.0),
    }
    within_partitions = all(
        committed_by_purpose[name][0] <= caps[name][0] + 1e-6
        and committed_by_purpose[name][1] <= caps[name][1] + 1e-6
        for name in caps
    )
    require(budget.get("within_partition_caps") is within_partitions,
            "budget partition flag contradicts purpose totals")

    parsed_anchor = parse_go_request(str(time_plan.get("anchor_go_command", "")))
    limits = {row.get("name"): row.get("value") for row in parsed_anchor.get("limits", [])}
    anchor_bounded = (
        not parsed_anchor.get("unknown_tokens")
        and set(limits) == {"movetime"}
        and type(limits.get("movetime")) is int
        and 0 < limits["movetime"] <= int(time_plan.get("soft_budget_ms", -1))
    )
    anchor_lane = lanes.get("anchor")
    anchor_reserved = bool(
        isinstance(anchor_lane, dict)
        and type(anchor_lane.get("settlements")) is int
        and anchor_lane["settlements"] >= 1
    )

    thresholds = route.get("thresholds") or {}
    if envelope.gpu_ms <= 0:
        gpu_accounted = True
    else:
        gpu_accounted = _number(
            thresholds.get("stage_gpu_ms_estimate"),
            "stage_gpu_ms_estimate",
        ) > 0
        if route.get("verification_enabled", False):
            gpu_accounted = gpu_accounted and _number(
                thresholds.get("verify_stage_gpu_ms_estimate"),
                "verify_stage_gpu_ms_estimate",
            ) > 0
        if route.get("refinement_enabled", False):
            gpu_accounted = gpu_accounted and _number(
                thresholds.get("refine_stage_gpu_ms_estimate"),
                "refine_stage_gpu_ms_estimate",
            ) > 0 and _number(
                thresholds.get("refine_oracle_gpu_ms_estimate"),
                "refine_oracle_gpu_ms_estimate",
            ) > 0

    settlement_complete = verify_specialist_settlements(
        route.get("specialist_actions") or [],
        budget.get("open_reservations"),
    )
    wall_within = _number(budget.get("elapsed_ms"), "budget elapsed_ms") <= envelope.wall_ms + 1e-6
    physical_within = physical <= envelope.cpu_ms + 1e-6
    clock_complete = (manifest.get("clock_outcome") or {}).get("output_within_deadline") is True

    claim = route.get("envelope_claim") or {}
    derived = {
        "anchor_request_bounded": anchor_bounded,
        "anchor_cost_reserved": anchor_reserved,
        "gpu_accounted": gpu_accounted,
        "reservations_within_envelope": within_envelope,
        "specialist_partitions_within_caps": within_partitions,
        "specialist_settlement_complete": settlement_complete,
        "wall_within_envelope": wall_within,
        "physical_measurement_required": bool(settings.get("require_cpu_for_claim") or settings.get("require_gpu_for_claim")),
        "physical_measurement_qualified": expected_qualified,
        "physical_cpu_within_envelope": physical_within,
        "clock_output_complete": clock_complete,
    }
    for key, value in derived.items():
        require(claim.get(key) is value, f"envelope claim {key} contradicts reconstructed evidence")
    expected_claim = all((
        clock_complete, anchor_bounded, anchor_reserved, gpu_accounted,
        within_envelope, within_partitions, settlement_complete, wall_within,
        expected_qualified, physical_within,
    ))
    require(claim.get("claimed") is expected_claim,
            "envelope claimed flag contradicts reconstructed evidence")
    return {
        "qualified": expected_qualified,
        "claimed": expected_claim,
        "physical_cpu_ms": round(physical, 3),
    }


def finite_metrics(summary: dict) -> None:
    require((summary.get("resources") or {}).get("cpu_complete") is True,
            "session CPU coverage is incomplete")
    for metric in summary["search_metrics"]:
        value = metric["cpu_ms_observed"]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "missing/nonfinite per-search CPU observation")
    for key in ("reaped_subtree_cpu_ms", "proxy_cpu_ms"):
        value = summary["resources"][key]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "invalid session CPU")
