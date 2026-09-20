"""Shared line-oriented UCI telemetry parsing without engine semantic conflation."""

from __future__ import annotations

import re
from typing import Any

from common.telemetry import SearchIdentity, TelemetryError, TelemetryStream


_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$", re.IGNORECASE)
_NULL_MOVES = {"(none)", "none", "0000", "a1a1"}
_INT_FIELDS = {
    "depth",
    "seldepth",
    "time",
    "nodes",
    "nps",
    "hashfull",
    "tbhits",
    "multipv",
    "movesleft",
    "eps",
    "currmovenumber",
    "player",
    "gameid",
    "cpuload",
}
_STRING_FIELDS = {"currmove", "side"}


class TelemetryParseError(TelemetryError):
    """Raised when a structured engine telemetry line is malformed."""


def is_uci_move(token: str) -> bool:
    return bool(_MOVE_RE.fullmatch(token))


def normalize_nullable_move(token: str) -> str | None:
    lowered = token.lower()
    if lowered in _NULL_MOVES:
        return None
    if not is_uci_move(lowered):
        raise TelemetryParseError(f"invalid UCI move token: {token!r}")
    return lowered


def _parse_int(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        return None


def parse_info_line(line: str) -> dict[str, Any]:
    """Parse the stable syntactic subset of one UCI info line.

    Unknown material is preserved through the raw line and unknown token list.
    PV parsing consumes only UCI-looking move tokens, so trailing LC0 comments
    do not become bogus moves.
    """
    if line == "info":
        return {"raw": line}
    if not line.startswith("info "):
        raise TelemetryParseError(f"not an info line: {line!r}")

    tokens = line.split()
    result: dict[str, Any] = {"raw": line}
    unknown: list[str] = []
    i = 1
    while i < len(tokens):
        token = tokens[i]

        if token in _INT_FIELDS:
            if i + 1 >= len(tokens):
                unknown.append(token)
                i += 1
                continue
            value = _parse_int(tokens[i + 1])
            if value is None:
                unknown.extend(tokens[i : i + 2])
            else:
                result[token] = value
            i += 2
            continue

        if token in _STRING_FIELDS:
            if i + 1 >= len(tokens):
                unknown.append(token)
                i += 1
                continue
            result[token] = tokens[i + 1]
            i += 2
            continue

        if token == "score":
            if i + 2 >= len(tokens):
                unknown.extend(tokens[i:])
                break
            kind = tokens[i + 1]
            value = _parse_int(tokens[i + 2])
            if kind not in {"cp", "mate"} or value is None:
                unknown.extend(tokens[i : i + 3])
                i += 3
                continue
            result["score"] = {"kind": kind, "value": value, "bound": "none"}
            i += 3
            if i < len(tokens) and tokens[i] in {"lowerbound", "upperbound"}:
                result["score"]["bound"] = "lower" if tokens[i] == "lowerbound" else "upper"
                i += 1
            continue

        if token == "wdl":
            if i + 3 >= len(tokens):
                unknown.extend(tokens[i:])
                break
            values = [_parse_int(x) for x in tokens[i + 1 : i + 4]]
            if any(value is None for value in values):
                unknown.extend(tokens[i : i + 4])
            else:
                result["wdl"] = values
            i += 4
            continue

        if token == "pv":
            i += 1
            pv: list[str] = []
            while i < len(tokens) and is_uci_move(tokens[i]):
                pv.append(tokens[i].lower())
                i += 1
            result["pv"] = pv
            continue

        if token == "string":
            result["comment"] = " ".join(tokens[i + 1 :])
            break

        unknown.append(token)
        i += 1

    if unknown:
        result["unknown_tokens"] = unknown
    return result


def parse_bestmove_line(line: str) -> dict[str, Any]:
    if not line.startswith("bestmove "):
        raise TelemetryParseError(f"not a bestmove line: {line!r}")
    tokens = line.split()
    if len(tokens) < 2:
        raise TelemetryParseError("bestmove line is missing a move")

    result: dict[str, Any] = {
        "raw": line,
        "bestmove": normalize_nullable_move(tokens[1]),
    }
    i = 2
    extra: dict[str, Any] = {}
    while i < len(tokens):
        token = tokens[i]
        if token == "ponder" and i + 1 < len(tokens):
            result["ponder"] = normalize_nullable_move(tokens[i + 1])
            i += 2
            continue
        if token in {"player", "gameid"} and i + 1 < len(tokens):
            value = _parse_int(tokens[i + 1])
            extra[token] = value if value is not None else tokens[i + 1]
            i += 2
            continue
        if token == "side" and i + 1 < len(tokens):
            extra[token] = tokens[i + 1]
            i += 2
            continue
        extra.setdefault("unknown_tokens", []).append(token)
        i += 1
    if extra:
        result["extra"] = extra
    return result


class BaseUciTelemetryAdapter:
    """Convert one engine's UCI search stream into telemetry v1 events."""

    engine: str
    nodes_unit = "nodes"
    default_multipv_if_missing: int | None = None

    def __init__(
        self,
        *,
        search_id: str,
        engine_instance: str,
        position_id: str,
        variant: str = "standard",
    ):
        self.stream = TelemetryStream(
            SearchIdentity(
                engine=self.engine,
                engine_instance=engine_instance,
                search_id=search_id,
                position_id=position_id,
                variant=variant,
            )
        )

    def start(
        self,
        *,
        position: dict[str, Any],
        request: dict[str, Any],
        observed_ms: int | float = 0,
        controller: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.stream.start(
            position=position,
            request=request,
            observed_ms=observed_ms,
            controller=controller,
        )

    def score_semantics(self, kind: str) -> tuple[str, str]:
        raise NotImplementedError

    def wdl_semantics(self) -> str:
        return f"{self.engine}.uci_wdl"

    def info_native_schema(self) -> str:
        return f"{self.engine}.uci.v1"

    def raw_native_schema(self) -> str:
        return f"{self.engine}.uci.raw.v1"

    def bestmove_native_schema(self) -> str:
        return f"{self.engine}.uci.bestmove.v1"

    def _native_data(self, parsed: dict[str, Any]) -> dict[str, Any]:
        promoted = {"score", "wdl", "nodes", "time", "pv", "multipv"}
        return {key: value for key, value in parsed.items() if key not in promoted}

    def _native_event(self, parsed: dict[str, Any], *, observed_ms: int | float) -> dict[str, Any]:
        return self.stream.emit(
            "native.event",
            observed_ms=observed_ms,
            payload={
                "native": {
                    "schema": self.info_native_schema(),
                    "data": self._native_data(parsed),
                }
            },
        )

    def _candidate_event(self, parsed: dict[str, Any], *, observed_ms: int | float) -> dict[str, Any]:
        pv = parsed.get("pv")
        if not pv:
            return self._native_event(parsed, observed_ms=observed_ms)

        multipv = parsed.get("multipv", self.default_multipv_if_missing)
        if multipv is None or multipv < 1:
            return self._native_event(parsed, observed_ms=observed_ms)

        candidate: dict[str, Any] = {
            "multipv_index": multipv,
            "move": pv[0],
            "pv": pv,
        }
        evaluations: list[dict[str, Any]] = []
        score = parsed.get("score")
        if score:
            kind, semantics = self.score_semantics(score["kind"])
            evaluations.append(
                {
                    "kind": kind,
                    "value": score["value"],
                    "bound": score["bound"],
                    "perspective": "unknown",
                    "semantics": semantics,
                }
            )
        wdl = parsed.get("wdl")
        if wdl:
            evaluations.append(
                {
                    "kind": "wdl",
                    "win": wdl[0],
                    "draw": wdl[1],
                    "loss": wdl[2],
                    "scale": sum(wdl),
                    "bound": "none",
                    "perspective": "unknown",
                    "semantics": self.wdl_semantics(),
                }
            )
        if evaluations:
            candidate["evaluations"] = evaluations

        payload: dict[str, Any] = {
            "candidate": candidate,
            "native": {
                "schema": self.info_native_schema(),
                "data": self._native_data(parsed),
            },
        }
        if "nodes" in parsed:
            payload["work"] = [
                {
                    "value": parsed["nodes"],
                    "unit": self.nodes_unit,
                    "semantics": f"{self.engine}.uci_nodes",
                }
            ]
        if "time" in parsed:
            payload["engine_time"] = {
                "value": parsed["time"],
                "unit": "ms",
                "semantics": f"{self.engine}.uci_time",
            }
        return self.stream.emit("candidate.update", observed_ms=observed_ms, payload=payload)

    def _complete_event(self, parsed: dict[str, Any], *, observed_ms: int | float) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "bestmove": parsed["bestmove"],
            "native": {
                "schema": self.bestmove_native_schema(),
                "data": {
                    "raw": parsed["raw"],
                    **parsed.get("extra", {}),
                },
            },
        }
        if "ponder" in parsed:
            payload["ponder"] = parsed["ponder"]
        return self.stream.emit("search.complete", observed_ms=observed_ms, payload=payload)

    def consume(self, line: str, *, observed_ms: int | float) -> list[dict[str, Any]]:
        if not self.stream.started:
            raise TelemetryError("adapter must be started before consuming engine output")
        if self.stream.completed:
            raise TelemetryError("adapter received output after search.complete")
        if not line:
            return []
        if line.startswith("info"):
            parsed = parse_info_line(line)
            if parsed.get("pv"):
                return [self._candidate_event(parsed, observed_ms=observed_ms)]
            return [self._native_event(parsed, observed_ms=observed_ms)]
        if line.startswith("bestmove "):
            return [self._complete_event(parse_bestmove_line(line), observed_ms=observed_ms)]
        return [
            self.stream.emit(
                "native.event",
                observed_ms=observed_ms,
                payload={
                    "native": {
                        "schema": self.raw_native_schema(),
                        "data": {"raw": line},
                    }
                },
            )
        ]
