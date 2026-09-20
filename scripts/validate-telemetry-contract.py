#!/usr/bin/env python3
"""Validate Allfather telemetry v1 fixtures and stream invariants using stdlib only."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "schemas" / "telemetry" / "v1.contract.json"
VALID_DIR = ROOT / "tests" / "telemetry" / "valid"
INVALID_DIR = ROOT / "tests" / "telemetry" / "invalid"


class ContractError(RuntimeError):
    pass


def fail(path: Path, line_no: int, message: str) -> None:
    raise ContractError(f"{path.relative_to(ROOT)}:{line_no}: {message}")


def load_contract() -> dict[str, Any]:
    data = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if (
        data.get("contract") != "allfather.telemetry"
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise ContractError("unsupported telemetry contract metadata")
    return data


def reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is forbidden")


def load_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    records: list[tuple[int, dict[str, Any]]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw, parse_constant=reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            fail(path, line_no, f"invalid JSON: {exc}")
        if not isinstance(value, dict):
            fail(path, line_no, "each JSONL record must be an object")
        records.append((line_no, value))
    if not records:
        raise ContractError(f"{path.relative_to(ROOT)}: empty telemetry stream")
    return records


def require(record: dict[str, Any], key: str, path: Path, line_no: int) -> Any:
    if key not in record:
        fail(path, line_no, f"missing required field {key!r}")
    return record[key]


def require_nonempty_str(value: Any, label: str, path: Path, line_no: int) -> str:
    if not isinstance(value, str) or not value:
        fail(path, line_no, f"{label} must be a non-empty string")
    return value


def require_finite_number(value: Any, label: str, path: Path, line_no: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(path, line_no, f"{label} must be a finite number")
    try:
        numeric = float(value)
    except OverflowError:
        fail(path, line_no, f"{label} must be finite")
    if not math.isfinite(numeric):
        fail(path, line_no, f"{label} must be finite")
    return numeric


def require_nonnegative_number(value: Any, label: str, path: Path, line_no: int) -> float:
    numeric = require_finite_number(value, label, path, line_no)
    if numeric < 0:
        fail(path, line_no, f"{label} must be a non-negative number")
    return numeric


def validate_move(move: Any, move_re: re.Pattern[str], label: str, path: Path, line_no: int) -> str:
    move = require_nonempty_str(move, label, path, line_no)
    if not move_re.fullmatch(move):
        fail(path, line_no, f"{label} is not canonical lowercase UCI move syntax: {move!r}")
    return move


def validate_native(
    native: Any,
    *,
    engine: str,
    prefixes: dict[str, str],
    path: Path,
    line_no: int,
) -> None:
    if not isinstance(native, dict):
        fail(path, line_no, "native must be an object")
    schema = require_nonempty_str(native.get("schema"), "native.schema", path, line_no)
    if not schema.startswith(prefixes[engine]):
        fail(path, line_no, f"native.schema {schema!r} does not match engine {engine!r}")
    if "data" not in native or not isinstance(native["data"], dict):
        fail(path, line_no, "native.data must be an object")


def validate_evaluations(
    evaluations: Any,
    *,
    engine: str,
    contract: dict[str, Any],
    path: Path,
    line_no: int,
) -> None:
    if not isinstance(evaluations, list):
        fail(path, line_no, "candidate.evaluations must be an array")
    for index, item in enumerate(evaluations):
        label = f"candidate.evaluations[{index}]"
        if not isinstance(item, dict):
            fail(path, line_no, f"{label} must be an object")
        kind = item.get("kind")
        if kind not in contract["evaluation"]["kinds"]:
            fail(path, line_no, f"{label}.kind is unsupported: {kind!r}")
        bound = item.get("bound")
        if bound not in contract["evaluation"]["bounds"]:
            fail(path, line_no, f"{label}.bound is unsupported: {bound!r}")
        perspective = item.get("perspective")
        if perspective not in contract["evaluation"]["perspectives"]:
            fail(path, line_no, f"{label}.perspective is unsupported: {perspective!r}")
        semantics = require_nonempty_str(item.get("semantics"), f"{label}.semantics", path, line_no)
        if not semantics.startswith(engine + "."):
            fail(path, line_no, f"{label}.semantics must be engine-prefixed")
        if kind == "wdl":
            for field in ("win", "draw", "loss", "scale"):
                require_nonnegative_number(item.get(field), f"{label}.{field}", path, line_no)
            if item["scale"] <= 0:
                fail(path, line_no, f"{label}.scale must be positive")
            if item["win"] + item["draw"] + item["loss"] != item["scale"]:
                fail(path, line_no, f"{label} WDL components must sum to scale")
        else:
            require_finite_number(item.get("value"), f"{label}.value", path, line_no)


def validate_work(
    work: Any,
    *,
    engine: str,
    contract: dict[str, Any],
    path: Path,
    line_no: int,
) -> None:
    if not isinstance(work, list):
        fail(path, line_no, "work must be an array")
    for index, item in enumerate(work):
        label = f"work[{index}]"
        if not isinstance(item, dict):
            fail(path, line_no, f"{label} must be an object")
        require_nonnegative_number(item.get("value"), f"{label}.value", path, line_no)
        unit = require_nonempty_str(item.get("unit"), f"{label}.unit", path, line_no)
        semantics = require_nonempty_str(item.get("semantics"), f"{label}.semantics", path, line_no)
        if not semantics.startswith(engine + "."):
            fail(path, line_no, f"{label}.semantics must be engine-prefixed")
        expected_unit = contract["known_work_units"].get(semantics)
        if expected_unit is not None and unit != expected_unit:
            fail(
                path,
                line_no,
                f"{label}.unit must be {expected_unit!r} for semantics {semantics!r}",
            )


def validate_request(
    request: Any,
    *,
    move_re: re.Pattern[str],
    path: Path,
    line_no: int,
) -> None:
    if not isinstance(request, dict):
        fail(path, line_no, "request must be an object")
    if "root_moves" in request:
        roots = request["root_moves"]
        if not isinstance(roots, list):
            fail(path, line_no, "request.root_moves must be an array when present")
        for index, move in enumerate(roots):
            validate_move(move, move_re, f"request.root_moves[{index}]", path, line_no)
    limits = request.get("limits", [])
    if not isinstance(limits, list):
        fail(path, line_no, "request.limits must be an array")
    for index, item in enumerate(limits):
        label = f"request.limits[{index}]"
        if not isinstance(item, dict):
            fail(path, line_no, f"{label} must be an object")
        require_nonempty_str(item.get("name"), f"{label}.name", path, line_no)
        value = item.get("value")
        if isinstance(value, bool):
            pass
        else:
            require_nonnegative_number(value, f"{label}.value", path, line_no)
        require_nonempty_str(item.get("semantics"), f"{label}.semantics", path, line_no)


def validate_controller(
    controller: Any,
    *,
    contract: dict[str, Any],
    path: Path,
    line_no: int,
) -> None:
    if not isinstance(controller, dict):
        fail(path, line_no, "controller must be an object")
    mode = controller.get("execution_mode")
    if mode is not None and mode not in contract["controller"]["execution_modes"]:
        fail(path, line_no, f"unsupported controller.execution_mode {mode!r}")
    phase = controller.get("phase")
    if phase is not None and phase not in contract["controller"]["phases"]:
        fail(path, line_no, f"unsupported controller.phase {phase!r}")
    if "shard_id" in controller:
        require_nonempty_str(controller["shard_id"], "controller.shard_id", path, line_no)


def reject_forbidden_common_fields(
    value: Any,
    *,
    forbidden: set[str],
    path: Path,
    line_no: int,
    trail: tuple[str, ...] = (),
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                dotted = ".".join((*trail, key))
                fail(path, line_no, f"derived/controller field is forbidden in raw telemetry: {dotted}")
            if trail == ("native",) and key == "data":
                continue
            reject_forbidden_common_fields(
                child,
                forbidden=forbidden,
                path=path,
                line_no=line_no,
                trail=(*trail, key),
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_forbidden_common_fields(
                child,
                forbidden=forbidden,
                path=path,
                line_no=line_no,
                trail=(*trail, str(index)),
            )


def validate_record(
    record: dict[str, Any],
    *,
    contract: dict[str, Any],
    move_re: re.Pattern[str],
    path: Path,
    line_no: int,
) -> None:
    for key in contract["common_required"]:
        require(record, key, path, line_no)

    reject_forbidden_common_fields(
        record,
        forbidden=set(contract.get("forbidden_common_fields", [])),
        path=path,
        line_no=line_no,
    )

    schema_version = record["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != contract["schema_version"]
    ):
        fail(path, line_no, f"unsupported schema_version {schema_version!r}")

    event_type = record["event_type"]
    if event_type not in contract["event_types"]:
        fail(path, line_no, f"unsupported event_type {event_type!r}")

    engine = record["engine"]
    if engine not in contract["engines"]:
        fail(path, line_no, f"unsupported engine {engine!r}")

    require_nonempty_str(record["search_id"], "search_id", path, line_no)
    require_nonempty_str(record["engine_instance"], "engine_instance", path, line_no)
    require_nonempty_str(record["position_id"], "position_id", path, line_no)

    sequence = record["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        fail(path, line_no, "sequence must be a non-negative integer")
    require_nonnegative_number(record["observed_ms"], "observed_ms", path, line_no)

    if event_type == "search.started":
        variant = record.get("variant")
        if variant not in contract["variants"]:
            fail(path, line_no, f"unsupported variant {variant!r}")
        expected_encoding = contract["variants"][variant]
        if record.get("move_encoding") != expected_encoding:
            fail(
                path,
                line_no,
                f"move_encoding must be {expected_encoding!r} for variant {variant!r}",
            )
        position = record.get("position")
        if not isinstance(position, dict):
            fail(path, line_no, "position must be an object")
        require_nonempty_str(position.get("base_fen"), "position.base_fen", path, line_no)
        moves = position.get("moves")
        if not isinstance(moves, list):
            fail(path, line_no, "position.moves must be an array")
        for index, move in enumerate(moves):
            validate_move(move, move_re, f"position.moves[{index}]", path, line_no)
        validate_request(record.get("request"), move_re=move_re, path=path, line_no=line_no)
        if "controller" in record:
            validate_controller(record["controller"], contract=contract, path=path, line_no=line_no)

    elif event_type == "candidate.update":
        candidate = record.get("candidate")
        if not isinstance(candidate, dict):
            fail(path, line_no, "candidate must be an object")
        multipv = candidate.get("multipv_index")
        if isinstance(multipv, bool) or not isinstance(multipv, int) or multipv < 1:
            fail(path, line_no, "candidate.multipv_index must be a positive integer")
        move = validate_move(candidate.get("move"), move_re, "candidate.move", path, line_no)
        pv = candidate.get("pv")
        if not isinstance(pv, list) or not pv:
            fail(path, line_no, "candidate.pv must be a non-empty array")
        normalized_pv = [
            validate_move(item, move_re, f"candidate.pv[{index}]", path, line_no)
            for index, item in enumerate(pv)
        ]
        if normalized_pv[0] != move:
            fail(path, line_no, "candidate.pv[0] must equal candidate.move")
        if "evaluations" in candidate:
            validate_evaluations(
                candidate["evaluations"],
                engine=engine,
                contract=contract,
                path=path,
                line_no=line_no,
            )
        if "work" in record:
            validate_work(
                record["work"],
                engine=engine,
                contract=contract,
                path=path,
                line_no=line_no,
            )
        if "engine_time" in record:
            engine_time = record["engine_time"]
            if not isinstance(engine_time, dict):
                fail(path, line_no, "engine_time must be an object")
            require_nonnegative_number(engine_time.get("value"), "engine_time.value", path, line_no)
            if engine_time.get("unit") != "ms":
                fail(path, line_no, "engine_time.unit must be 'ms'")
            semantics = require_nonempty_str(
                engine_time.get("semantics"), "engine_time.semantics", path, line_no
            )
            if not semantics.startswith(engine + "."):
                fail(path, line_no, "engine_time.semantics must be engine-prefixed")
        if "native" in record:
            validate_native(
                record["native"],
                engine=engine,
                prefixes=contract["native_schema_prefix_by_engine"],
                path=path,
                line_no=line_no,
            )

    elif event_type == "search.complete":
        if "bestmove" not in record:
            fail(path, line_no, "search.complete requires bestmove (string or null)")
        if record["bestmove"] is not None:
            validate_move(record["bestmove"], move_re, "bestmove", path, line_no)
        if "ponder" in record and record["ponder"] is not None:
            validate_move(record["ponder"], move_re, "ponder", path, line_no)
        if "native" in record:
            validate_native(
                record["native"],
                engine=engine,
                prefixes=contract["native_schema_prefix_by_engine"],
                path=path,
                line_no=line_no,
            )

    elif event_type == "native.event":
        validate_native(
            record.get("native"),
            engine=engine,
            prefixes=contract["native_schema_prefix_by_engine"],
            path=path,
            line_no=line_no,
        )

    elif event_type == "terminal.fact":
        fact = record.get("fact")
        if fact not in contract["terminal_facts"]:
            fail(path, line_no, f"unsupported terminal fact {fact!r}")
        perspective = record.get("perspective")
        if perspective not in contract["terminal_perspectives"]:
            fail(path, line_no, f"unsupported terminal perspective {perspective!r}")
        source = require_nonempty_str(record.get("source"), "terminal.fact source", path, line_no)
        if not any(source.startswith(prefix) for prefix in contract["terminal_source_prefixes"]):
            fail(path, line_no, f"terminal.fact source is not independently qualified: {source!r}")


def validate_stream(path: Path, contract: dict[str, Any]) -> None:
    move_re = re.compile(contract["move_pattern"])
    states: dict[str, dict[str, Any]] = {}

    for line_no, record in load_jsonl(path):
        validate_record(
            record,
            contract=contract,
            move_re=move_re,
            path=path,
            line_no=line_no,
        )
        search_id = record["search_id"]
        event_type = record["event_type"]

        if search_id not in states:
            if event_type != "search.started":
                fail(path, line_no, "first event for a search must be search.started")
            states[search_id] = {
                "engine": record["engine"],
                "engine_instance": record["engine_instance"],
                "position_id": record["position_id"],
                "last_sequence": record["sequence"],
                "last_observed_ms": float(record["observed_ms"]),
                "completed": False,
            }
            continue

        state = states[search_id]
        if event_type == "search.started":
            fail(path, line_no, "search.started may occur only once per search")
        if state["completed"]:
            fail(path, line_no, "events after search.complete are forbidden")
        if record["sequence"] <= state["last_sequence"]:
            fail(path, line_no, "sequence must strictly increase within a search")
        if float(record["observed_ms"]) < state["last_observed_ms"]:
            fail(path, line_no, "observed_ms must not decrease within a search")

        for key in ("engine", "engine_instance", "position_id"):
            if record[key] != state[key]:
                fail(path, line_no, f"{key} changed within search {search_id!r}")

        if event_type == "search.complete":
            state["completed"] = True

        state["last_sequence"] = record["sequence"]
        state["last_observed_ms"] = float(record["observed_ms"])

    incomplete = sorted(search_id for search_id, state in states.items() if not state["completed"])
    if incomplete:
        raise ContractError(
            f"{path.relative_to(ROOT)}: incomplete search streams without search.complete: {incomplete}"
        )


def validate_fixture_sets() -> None:
    contract = load_contract()
    valid = sorted(VALID_DIR.glob("*.jsonl"))
    invalid = sorted(INVALID_DIR.glob("*.jsonl"))
    if not valid or not invalid:
        raise ContractError("both valid and invalid telemetry fixture sets are required")

    for path in valid:
        validate_stream(path, contract)

    failures = 0
    for path in invalid:
        try:
            validate_stream(path, contract)
        except ContractError:
            failures += 1
        else:
            raise ContractError(
                f"{path.relative_to(ROOT)}: invalid fixture unexpectedly satisfied the contract"
            )

    print(
        f"telemetry contract v{contract['schema_version']} passed: "
        f"{len(valid)} valid streams accepted, {failures} invalid streams rejected"
    )


def main(argv: list[str]) -> int:
    contract = load_contract()
    if argv:
        for value in argv:
            validate_stream((ROOT / value).resolve(), contract)
        print(f"telemetry contract v{contract['schema_version']} passed for {len(argv)} stream(s)")
        return 0

    validate_fixture_sets()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ContractError, OSError, ValueError) as exc:
        print(f"telemetry contract failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
