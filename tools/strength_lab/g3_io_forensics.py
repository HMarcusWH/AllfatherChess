"""Read-only, non-authorizing forensics for the G3 pilot's durable-I/O tails.

Consumes retained ZIP artifacts or one campaign directory. This is *not* a
chess/game/resource qualifier: it cannot turn a rejected v1 replay into a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

MAX_MEMBERS = 25000
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024
REPLAY_PATH = re.compile(
    r"^(?P<campaign>[^/]+)/(?P<block>g3-pilot-\d\d)/sessions/allfather-g3/"
    r"(?P<session>[^/]+)/replays/(?P<run>[^/]+)/"
    r"(?P<filename>manifest\.json|route\.json|resource\.json)$"
)
SEAL_NOTE = re.compile(
    r"resource seal phases \(non-authorizing\): "
    r"freeze_wall_ms=(?P<freeze>[\d.]+), "
    r"serialization_ms=(?P<serialization>[\d.]+), "
    r"durable_write_ms=(?P<durable>[\d.]+), "
    r"persisted_wall_ms=(?P<persisted>[\d.]+)"
)
PROCESS_NOTE = re.compile(r"process endpoints frozen at ([\d.]+)ms from external go")


class ForensicError(ValueError):
    """The evidence cannot be inspected without making an assumption."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ForensicError(message)


def strict_json(blob: bytes, name: str) -> dict:
    require(len(blob) <= MAX_JSON_BYTES, f"oversized evidence JSON: {name}")

    def pairs(values):
        output = {}
        for key, value in values:
            require(key not in output, f"duplicate JSON key {key!r}: {name}")
            output[key] = value
        return output

    try:
        obj = json.loads(blob, object_pairs_hook=pairs,
                         parse_constant=lambda t: (_ for _ in ()).throw(ForensicError(f"nonfinite JSON constant: {t}")))
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ForensicError(f"invalid JSON: {name}: {exc}") from exc
    require(isinstance(obj, dict), f"evidence JSON root must be object: {name}")
    return obj


def _number(value, field: str) -> float:
    require(type(value) in (float, int) and math.isfinite(value) and value >= 0,
            f"invalid nonnegative number {field}")
    return float(value)


def _quantile(sorted_values: list[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    k = fraction * (len(sorted_values) - 1)
    lo = math.floor(k)
    hi = math.ceil(k)
    return round(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo), 3)


def _metrics(values: list[float]) -> dict:
    values = sorted(values)
    return {
        "count": len(values),
        "median_ms": _quantile(values, 0.5),
        "p95_ms": _quantile(values, 0.95),
        "p99_ms": _quantile(values, 0.99),
        "max_ms": None if not values else round(values[-1], 3),
        "above_100ms": sum(value > 100 for value in values),
        "above_350ms": sum(value > 350 for value in values),
    }


def _safe_member(name: str) -> None:
    require(name and "\\" not in name and "\x00" not in name,
            f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(x not in ("", ".", "..") for x in name.rstrip("/").split("/")),
            f"path traversal or empty archive segment: {name!r}")


def _campaign_files(source: Path):
    """Return campaign/name->bytes reader after bounding & validating all paths."""
    if source.is_symlink():
        raise ForensicError("source path is symlink")
    source = source.resolve(strict=True)
    if source.is_file():
        require(source.suffix.lower() == ".zip", "only ZIP artifacts accepted")
        digest = hashlib.sha256()
        with source.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        archive = zipfile.ZipFile(source, "r")
        try:
            members = archive.infolist()
            require(len(members) <= MAX_MEMBERS, "archive member cap exceeded")
            require(sum(x.file_size for x in members) <= MAX_TOTAL_BYTES,
                    "archive uncompressed-size cap exceeded")
            seen = set()
            for item in members:
                _safe_member(item.filename)
                require(item.filename not in seen,
                        f"duplicate archive member: {item.filename}")
                seen.add(item.filename)
                require(stat.S_IFMT(item.external_attr >> 16) != stat.S_IFLNK,
                        f"symlink in archive: {item.filename}")
            campaign_dirs = {x.split("/")[0] for x in seen if "/" in x}
            require(len(campaign_dirs) == 1, "archive must have exactly one campaign root")
            campaign = next(iter(campaign_dirs))
            names = {x for x in seen if not x.endswith("/")}
            def read(name):
                require(name in names, f"missing evidence: {name}")
                info = archive.getinfo(name)
                require(info.file_size <= MAX_JSON_BYTES, f"oversized evidence: {name}")
                with archive.open(info) as file:
                    data = file.read(MAX_JSON_BYTES + 1)
                require(len(data) <= MAX_JSON_BYTES, f"unbounded decompressed entry: {name}")
                return data
            return archive, campaign, names, read, digest.hexdigest()
        except BaseException:
            archive.close()
            raise
    require(source.is_dir(), "source must be ZIP or directory")
    paths = {}
    campaign = source.name
    require("/" not in campaign, "invalid campaign directory name")
    count = 0
    total = 0
    for root, dirs, files in os.walk(source, followlinks=False):
        for part in dirs + files:
            item = Path(root) / part
            require(not item.is_symlink(), f"symlink in evidence directory: {item}")
        for name in files:
            item = Path(root) / name
            relative = PurePosixPath(campaign) / item.relative_to(source).as_posix()
            _safe_member(str(relative))
            count += 1
            total += item.stat().st_size
            require(count <= MAX_MEMBERS and total <= MAX_TOTAL_BYTES,
                    "evidence directory size/member cap exceeded")
            paths[str(relative)] = item
    def read(name):
        require(name in paths, f"missing evidence: {name}")
        item = paths[name]
        require(not item.is_symlink() and item.stat().st_size <= MAX_JSON_BYTES,
                f"unsafe/oversized evidence: {name}")
        return item.read_bytes()
    return None, campaign, set(paths), read, None


def _replay_record(name: str, read) -> dict:
    match = REPLAY_PATH.fullmatch(name + "/manifest.json")
    require(match is not None, f"invalid replay prefix: {name}")
    run_id = match.group("run")
    docs = {kind: strict_json(read(name + "/" + kind + ".json"), name + "/" + kind)
            for kind in ("manifest", "route", "resource")}
    manifest, route, resource = (docs[t] for t in ("manifest", "route", "resource"))
    for typ, value in docs.items():
        require(value.get("run_id") == run_id,
                f"{typ} replay/run identity mismatch: {run_id}")
    clock = manifest.get("clock_outcome") or {}
    output = clock.get("output_within_deadline")
    require(type(output) is bool, f"missing output timing claim: {run_id}")
    emitted = clock.get("emitted_ms")
    if emitted is not None:
        emitted = _number(emitted, "emitted_ms")
    phases = []
    for note in manifest.get("notes") or []:
        require(isinstance(note, str), f"nontext manifest note: {run_id}")
        hit = SEAL_NOTE.search(note)
        if hit:
            phases.append({key: _number(float(value), key)
                           for key, value in hit.groupdict().items()})
    require(len(phases) == 1, f"missing or repeated resource seal phase: {run_id}")
    phase = phases[0]
    assert phase is not None
    require(abs(phase["freeze"] + phase["serialization"] + phase["durable"] - phase["persisted"]) <= 2.0,
            f"resource phase arithmetic mismatch: {run_id}")
    endpoints = [float(hit.group(1)) for note in manifest.get("notes") or []
                 for hit in [PROCESS_NOTE.search(note)] if hit]
    require(len(endpoints) <= 1, f"ambiguous process endpoint freeze: {run_id}")
    budget = route.get("budget") or {}
    envelope = route.get("envelope") or {}
    claim = route.get("envelope_claim") or {}
    elapsed = _number(budget.get("elapsed_ms"), "terminal elapsed_ms")
    limit = _number(envelope.get("wall_ms"), "declared envelope wall_ms")
    require(limit > 0, f"nonpositive wall envelope: {run_id}")
    require(elapsed + 2.0 >= phase["persisted"],
            f"terminal budget precedes durable receipt: {run_id}")
    within = claim.get("wall_within_envelope")
    claimed = claim.get("claimed")
    require(type(within) is bool and type(claimed) is bool,
            f"missing strict envelope claim bits: {run_id}")
    require(within == (elapsed <= limit), f"wall flag contradicts terminal budget: {run_id}")
    require(not (claimed and not within), f"false wall case claimed qualified: {run_id}")
    cpu_ok = resource.get("qualified")
    require(type(cpu_ok) is bool, f"resource CPU qualification not boolean: {run_id}")
    return {
        "occurrence": name,
        "block": match.group("block"),
        "replay_id": run_id,
        "emitted_ms": emitted,
        "outward_before_deadline": output,
        "process_endpoints_frozen_ms": None if not endpoints else round(endpoints[0], 3),
        "freeze_wall_ms": phase["freeze"],
        "serialization_ms": phase["serialization"],
        "durable_write_ms": phase["durable"],
        "persisted_wall_ms": phase["persisted"],
        "terminal_wall_ms": elapsed,
        "declared_wall_ms": limit,
        "wall_excess_ms": round(max(0.0, elapsed - limit), 3),
        "wall_within_envelope": within,
        "full_claimed": claimed,
        "physical_cpu_qualified": cpu_ok,
        "physical_cpu_ms": resource.get("physical_cpu_ms"),
    }


def inspect(source: Path, *, expected_sha256: str | None = None,
            expected_source_commit: str | None = None,
            expected_campaign: str | None = None) -> dict:
    """Forensically observe an attempt; return *never-authorizing* diagnostics."""
    archive, campaign, names, read, archive_sha = _campaign_files(Path(source))
    try:
        if expected_sha256 is not None:
            require(archive_sha == expected_sha256.lower(), "source archive SHA-256 mismatch")
        if expected_campaign is not None:
            require(campaign == expected_campaign, "campaign source identity mismatch")
        manifest = strict_json(read(campaign + "/manifest.json"), "campaign manifest")
        source_commit = (manifest.get("source") or {}).get("commit")
        require(isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None,
                "campaign is missing exact source commit")
        if expected_source_commit is not None:
            require(source_commit == expected_source_commit, "source commit mismatch")
        attempts = manifest.get("jobs") or []
        require(isinstance(attempts, list) and len(attempts) <= 8,
                "invalid campaign jobs")
        observed_blocks_exited_zero = sum(
            isinstance(j, dict) and isinstance(j.get("execution"), dict)
            and j["execution"].get("returncode") == 0
            and j["execution"].get("timed_out") is False
            for j in attempts
        )
        prefixes: set[str] = set()
        paths_by_prefix: dict[str, set[str]] = {}
        for name in names:
            found = REPLAY_PATH.fullmatch(name)
            if found:
                prefix = name.rsplit("/", 1)[0]
                prefixes.add(prefix)
                paths_by_prefix.setdefault(prefix, set()).add(found.group("filename"))
        require(bool(prefixes), "no G3 replay evidence observed")
        seen_ids = set()
        rows = []
        for prefix in sorted(prefixes):
            require(paths_by_prefix[prefix] == {"manifest.json", "route.json", "resource.json"},
                    f"incomplete replay triplet: {prefix}")
            row = _replay_record(prefix, read)
            require(row["replay_id"] not in seen_ids,
                    f"replay run_id reused across sessions: {row['replay_id']}")
            seen_ids.add(row["replay_id"])
            rows.append(row)
        denied = [r for r in rows if not r["wall_within_envelope"]
                  or not r["full_claimed"] or not r["outward_before_deadline"]
                  or not r["physical_cpu_qualified"]]
        slowest = sorted(rows, key=lambda r: r["durable_write_ms"], reverse=True)[:12]
        durations = [r["durable_write_ms"] for r in rows]
        report = {
            "schema_version": 1,
            "report_kind": "g3-pilot-durable-io-forensics",
            "diagnostic_only": True,
            "qualifies": False,
            "strength_qualified": False,
            "live_game_authorized": False,
            "release_authorized": False,
            "archive_sha256": archive_sha,
            "source_commit": source_commit,
            "source_tree": (manifest.get("source") or {}).get("tree"),
            "campaign_id": campaign,
            "campaign_status_observed": manifest.get("status"),
            "scheduled_blocks_exited_zero_observed": observed_blocks_exited_zero,
            "qualified_games": None,
            "g3_replays_observed": len(rows),
            "wall_claim_true": sum(r["wall_within_envelope"] for r in rows),
            "wall_claim_false": sum(not r["wall_within_envelope"] for r in rows),
            "cpu_qualified_count": sum(r["physical_cpu_qualified"] for r in rows),
            "durable_write_ms": _metrics(durations),
            "denied_replays": denied,
            "slowest_replays": slowest,
        }
        return report
    finally:
        if archive is not None:
            archive.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--expected-sha256")
    p.add_argument("--expected-source-commit")
    p.add_argument("--expected-campaign")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    try:
        result = inspect(args.input, expected_sha256=args.expected_sha256,
                         expected_source_commit=args.expected_source_commit,
                         expected_campaign=args.expected_campaign)
        payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            sys.stdout.write(payload)
        return 0
    except (ForensicError, OSError, zipfile.BadZipFile) as exc:
        print(f"G3_IO_FORENSICS_REJECTED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
