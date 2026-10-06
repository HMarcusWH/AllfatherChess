"""Reconcile legacy G3 calibration with immutable retained LOCAL-1 archives.

Controller-purpose ledger CPU, physical process CPU and preparation wall time
are distinct scopes. Only the controller-purpose ledger is used for the fixed
controller partition. Failed campaigns remain failed; usable measurements do
not become authority qualification evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = ROOT / "qualification/online-hybrid-v1-resource-calibration.json"
OBSERVATIONS = ROOT / "qualification/online-hybrid-v1-resource-observations.json"
POLICY = ROOT / "qualification/online-hybrid-authority.json"
CONFIG = ROOT / "config/allfather.online-hybrid.validation.json"
OBSERVATIONS_SHA256 = "49a5de850f544e1566b0ac9c3d31d40142548fc50f0c6e69fcdcbd31f1bf3f18"

CLAIM = {
    "legacy_v1_resource_reservation_calibration": True,
    "canonical_b4_promotion": False,
    "outer_envelope_increase": False,
    "resource_authorization": False,
    "outward_move_authority": False,
    "strength": False,
    "elo": False,
    "equal_compute": False,
    "deployment": False,
}
OUTER = {
    "wall_ms": 4000,
    "cpu_ms": 12000,
    "gpu_ms": 0,
    "verification_reserve_fraction": 0.3,
    "controller_overhead_reserve_ms": 250,
}
SERIES = {
    "EXPLORE": "lc0_explore_cpu_ms",
    "VERIFY": "lc0_verify_cpu_ms",
    "VERIFY_EXTENSION": "lc0_staged_verify_extension_cpu_ms",
}
EXTRACTION = {
    "version": "legacy-g3-calibration-extraction-v1",
    "jobs": "base-* and life-* only; exclude fault-cases, rule-probes and prerequisites",
    "controller": "route.budget.purpose_totals.controller.spent_cpu_ms",
    "lc0": "complete resource.stages for lc0-shadow with finite cpu_ms, partitioned by phase",
    "percentiles": "linear interpolation at (n-1)*p; round output to 3 decimals",
    "missing_evidence": "retain exclusion records, never impute a sample",
}
OBSERVATION_CLAIM = {
    "calibration_observations_only": True,
    "campaign_qualified": False,
    "runtime_authority": False,
}


class CalibrationError(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise CalibrationError(message)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path}: JSON root must be object")
    return value


def finite(value: Any) -> float:
    require(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0,
        "non-finite/negative observation",
    )
    return float(value)


def summarize(values: list[float]) -> dict[str, float | int]:
    require(isinstance(values, list) and values, "missing observation series")
    rows = sorted(finite(value) for value in values)

    def quantile(fraction: float) -> float:
        index = (len(rows) - 1) * fraction
        low = int(math.floor(index))
        high = int(math.ceil(index))
        return rows[low] + (rows[high] - rows[low]) * (index - low)

    return {
        "samples": len(rows),
        "median": round(statistics.median(rows), 3),
        "p95": round(quantile(.95), 3),
        "p99": round(quantile(.99), 3),
        "max": max(rows),
    }


def compact_series(values: list[float]) -> dict[str, Any]:
    return {
        "sha256": digest(values),
        "summary": summarize(values),
    }


def extract_source(archive: Path, source: dict[str, Any]) -> dict[str, Any]:
    archive = Path(archive)
    require(
        sha(archive) == source["artifact_sha256"],
        "archive SHA does not match the frozen source",
    )
    prefix = f"test-results/local-full-game/{source['campaign_id']}/"
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
        names = [info.filename for info in infos]
        require(len(names) == len(set(names)), "duplicate archive members")
        require(
            sum(int(info.file_size) for info in infos) <= 2 * 1024**3,
            "archive too large",
        )
        for name in names:
            rel = PurePosixPath(name)
            require(
                not rel.is_absolute()
                and ".." not in rel.parts
                and "\\" not in name
                and "\x00" not in name,
                "unsafe archive member",
            )
        members = set(names)
        campaign = json.loads(handle.read(prefix + "manifest.json"))
        report = json.loads(handle.read(prefix + "report.json"))
        require(
            (campaign.get("source") or {}).get("commit")
            == source["source_commit"],
            "campaign source mismatch",
        )
        require(
            campaign.get("campaign_id") == source["campaign_id"],
            "campaign id mismatch",
        )
        runtime_inputs = [
            row
            for row in campaign.get("inputs", [])
            if row.get("path")
            == "config/allfather.online-hybrid.validation.json"
        ]
        require(len(runtime_inputs) == 1, "runtime input identity missing")

        series: dict[str, list[float]] = {
            "controller_total_cpu_ms": [],
            **{name: [] for name in SERIES.values()},
        }
        exclusions: list[dict[str, Any]] = []
        bindings: list[dict[str, Any]] = []
        routes: list[str] = []
        runtimes: dict[str, tuple[str, dict[str, Any]]] = {}

        for path in sorted(names):
            if not path.startswith(prefix) or not path.endswith("/route.json"):
                continue
            job = path[len(prefix):].split("/", 1)[0]
            if not job.startswith(("base-", "life-")):
                continue
            routes.append(path)
            base = path.rsplit("/", 1)[0]
            route_bytes = handle.read(path)
            route = json.loads(route_bytes)
            manifest_bytes = handle.read(base + "/manifest.json")
            manifest = json.loads(manifest_bytes)
            require(
                manifest.get("run_id")
                == route.get("run_id")
                == base.rsplit("/", 1)[-1],
                "replay identity mismatch",
            )

            runtime_path = base.split("/replays/", 1)[0] + "/runtime.json"
            if runtime_path not in runtimes:
                raw = handle.read(runtime_path)
                runtimes[runtime_path] = (
                    hashlib.sha256(raw).hexdigest(),
                    json.loads(raw),
                )
            runtime_sha, runtime = runtimes[runtime_path]
            require(
                (manifest.get("controller") or {}).get("config_sha256")
                == runtime_sha,
                "replay/runtime digest mismatch",
            )
            require(
                runtime["instances"]["lc0-shadow"]["family"] == "lc0",
                "wrong calibrated owner",
            )

            controller = finite(
                route["budget"]["purpose_totals"]["controller"][
                    "spent_cpu_ms"
                ]
            )
            series["controller_total_cpu_ms"].append(controller)
            resource_path = base + "/resource.json"
            binding: dict[str, Any] = {
                "route": path,
                "route_sha256": hashlib.sha256(route_bytes).hexdigest(),
                "manifest_sha256": hashlib.sha256(
                    manifest_bytes
                ).hexdigest(),
                "runtime_sha256": runtime_sha,
                "controller_ledger_ms": controller,
                "stages": [],
            }
            if resource_path not in members:
                exclusions.append({
                    "path": resource_path,
                    "reason": (
                        "missing resource.json; no LC0 values imputed"
                    ),
                })
            else:
                raw = handle.read(resource_path)
                resource = json.loads(raw)
                require(
                    resource.get("run_id") == route["run_id"],
                    "resource/replay identity mismatch",
                )
                binding["resource_sha256"] = hashlib.sha256(raw).hexdigest()
                seen: set[Any] = set()
                for stage in resource.get("stages", []):
                    if (
                        stage.get("instance") != "lc0-shadow"
                        or stage.get("phase") not in SERIES
                    ):
                        continue
                    key = stage.get("key")
                    require(key not in seen, "duplicate LC0 stage")
                    seen.add(key)
                    if (
                        stage.get("complete") is not True
                        or stage.get("cpu_ms") is None
                    ):
                        exclusions.append({
                            "path": resource_path,
                            "stage": key,
                            "reason": "incomplete stage",
                        })
                        continue
                    value = finite(stage["cpu_ms"])
                    phase = str(stage["phase"])
                    series[SERIES[phase]].append(value)
                    binding["stages"].append([key, phase, value])
            bindings.append(binding)

        require(
            len(routes) == source["route_count"],
            "normal-game route coverage mismatch",
        )
        identity = {
            key: source[key]
            for key in (
                "workflow_run",
                "artifact_id",
                "artifact_sha256",
                "source_commit",
                "campaign_id",
            )
        }
        return {
            "source": identity,
            "source_tree": campaign["source"]["tree"],
            "campaign_passed": report.get("passed"),
            "campaign_errors": report.get("errors"),
            "runtime_input": runtime_inputs[0],
            "route_count": len(routes),
            "route_membership_sha256": digest(routes),
            "observation_bindings_sha256": digest(bindings),
            "exclusions": exclusions,
            "series": {
                name: compact_series(values)
                for name, values in series.items()
            },
        }


def reconcile(
    archives: list[Path],
    calibration: dict[str, Any],
) -> dict[str, Any]:
    by_sha = {sha(Path(path)): Path(path) for path in archives}
    require(
        len(by_sha) == len(archives) == 2,
        "exactly two distinct archives required",
    )
    sources = calibration["retained_local1_sources"]
    require(
        set(by_sha)
        == {source["artifact_sha256"] for source in sources},
        "wrong calibration archives",
    )
    return {
        "schema_version": 1,
        "extraction": EXTRACTION,
        "claim_boundary": OBSERVATION_CLAIM,
        "sources": [
            extract_source(
                by_sha[source["artifact_sha256"]],
                source,
            )
            for source in sources
        ],
    }


def validate(
    calibration_path: Path = CALIBRATION,
    policy_path: Path = POLICY,
    config_path: Path = CONFIG,
    observations_path: Path = OBSERVATIONS,
) -> dict[str, Any]:
    calibration = load(calibration_path)
    policy = load(policy_path)
    config = load(config_path)
    evidence = load(observations_path)

    require(
        sha(observations_path) == OBSERVATIONS_SHA256,
        "frozen archive-reconciled observations changed",
    )
    require(
        calibration.get("schema_version") == 1
        and calibration.get("profile_id")
        == "online-hybrid-v1-resource-calibration",
        "calibration identity drift",
    )
    require(
        calibration.get("claim_boundary") == CLAIM
        and calibration.get("outer_envelope") == OUTER,
        "calibration authority/outer envelope drift",
    )
    binding = calibration.get("reconciliation") or {}
    require(
        binding.get("path")
        == "qualification/online-hybrid-v1-resource-observations.json"
        and binding.get("sha256") == OBSERVATIONS_SHA256,
        "raw observation binding mismatch",
    )
    require(
        binding.get("scope") == EXTRACTION,
        "calibration extraction scope drift",
    )
    require(
        evidence.get("schema_version") == 1
        and evidence.get("extraction") == EXTRACTION,
        "extraction protocol drift",
    )
    require(
        evidence.get("claim_boundary") == OBSERVATION_CLAIM,
        "observation authority drift",
    )

    sources = calibration.get("retained_local1_sources")
    observed_sources = evidence.get("sources")
    require(
        isinstance(sources, list)
        and isinstance(observed_sources, list)
        and len(sources) == len(observed_sources) == 2,
        "two sources required",
    )
    maxima = {"controller": 0.0, "explore": 0.0, "verify": 0.0}
    seen: set[int] = set()
    expected_series = {
        "controller_total_cpu_ms",
        *SERIES.values(),
    }

    for declared, observed in zip(sources, observed_sources):
        require(
            observed["source"]
            == {
                key: declared[key]
                for key in observed["source"]
            },
            "source/artifact identity mismatch",
        )
        require(
            declared["artifact_id"] not in seen,
            "duplicate source",
        )
        seen.add(declared["artifact_id"])
        require(
            declared["route_count"] == observed["route_count"],
            "sample membership drift",
        )
        require(
            observed.get("campaign_passed") is False,
            "failed calibration campaign was relabelled qualified",
        )
        series = observed.get("series")
        require(
            isinstance(series, dict)
            and set(series) == expected_series,
            "series scope drift",
        )
        summaries: dict[str, dict[str, Any]] = {}
        for name, row in series.items():
            require(
                isinstance(row, dict)
                and isinstance(row.get("sha256"), str)
                and len(row["sha256"]) == 64,
                f"{name}: observation vector digest missing",
            )
            summary = row.get("summary")
            require(
                isinstance(summary, dict),
                f"{name}: observation summary missing",
            )
            summaries[name] = summary
        require(
            summaries == declared["observed"],
            "calibration summary differs from retained observation evidence",
        )
        require(
            summaries["controller_total_cpu_ms"]["samples"]
            == observed["route_count"],
            "controller coverage drift",
        )
        maxima["controller"] = max(
            maxima["controller"],
            float(summaries["controller_total_cpu_ms"]["max"]),
        )
        maxima["explore"] = max(
            maxima["explore"],
            float(summaries["lc0_explore_cpu_ms"]["max"]),
        )
        maxima["verify"] = max(
            maxima["verify"],
            float(summaries["lc0_verify_cpu_ms"]["max"]),
            float(
                summaries["lc0_staged_verify_extension_cpu_ms"]["max"]
            ),
        )

    selected = calibration["selection"]
    explore = {
        "stockfish": 100,
        "reckless": 100,
        "lc0": math.ceil(maxima["explore"] / 100) * 100,
    }
    verify = {
        "stockfish": 100,
        "reckless": 100,
        "lc0": math.ceil(maxima["verify"] / 100) * 100 + 100,
    }
    require(
        explore["lc0"] == 1800
        and verify["lc0"] == 1400
        and maxima["controller"] <= 250,
        "calibration maxima drift",
    )
    require(
        selected
        == {
            "controller_overhead_reserve_ms": 250,
            "explore_cpu_ms_by_owner": explore,
            "verify_cpu_ms_by_owner": verify,
            "fallback_cpu_ms_per_stage": {
                "explore": 500,
                "verify": 750,
            },
        },
        "reservation selection drift",
    )
    for key, value in OUTER.items():
        require(
            config["budget"].get(key) == value,
            f"outer envelope drift: {key}",
        )
    require(
        policy.get("resource_calibration")
        == {
            "path": (
                "qualification/"
                "online-hybrid-v1-resource-calibration.json"
            ),
            "profile_id": "online-hybrid-v1-resource-calibration",
        },
        "policy calibration reference drift",
    )
    for phase, prefix, owners in (
        ("explore", "stage", explore),
        ("verification", "verify_stage", verify),
    ):
        fallback = 500 if phase == "explore" else 750
        require(
            config["routing"].get(
                prefix + "_cpu_ms_estimate_by_owner"
            )
            == owners
            and config["routing"].get(
                prefix + "_cpu_ms_estimate"
            )
            == fallback,
            "runtime reservation mismatch",
        )
        require(
            policy[phase].get("reservation_cpu_ms_by_owner")
            == owners
            and policy[phase].get("reservation_cpu_ms_per_stage")
            == fallback,
            "policy reservation mismatch",
        )

    return {
        "profile_id": calibration["profile_id"],
        "qualified": True,
        "retained_sources": 2,
        "observed_maxima_ms": maxima,
        "controller_overhead_reserve_ms": 250,
        "explore_cpu_ms_by_owner": explore,
        "verify_cpu_ms_by_owner": verify,
        "outer_envelope": OUTER,
        "claim_boundary": CLAIM,
        "observation_scope": EXTRACTION,
        "observations_sha256": OBSERVATIONS_SHA256,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--observations", type=Path, default=OBSERVATIONS)
    parser.add_argument("--archive", type=Path, action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.archive:
            reconstructed = reconcile(
                args.archive,
                load(args.calibration),
            )
            require(
                reconstructed == load(args.observations),
                "archive reconciliation differs from frozen observations",
            )
        report = validate(
            args.calibration,
            args.policy,
            args.config,
            args.observations,
        )
    except (
        CalibrationError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"legacy G3 calibration FAILED: {exc}")
        return 2

    payload = (
        json.dumps(
            report,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
