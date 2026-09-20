"""LC0 UCI and defect telemetry v1 adapter."""

from __future__ import annotations

import json
from typing import Any

from .uci import (
    BaseUciTelemetryAdapter,
    TelemetryParseError,
    parse_bestmove_line,
    parse_info_line,
)


_SCORE_TYPES = {
    "centipawn",
    "centipawn_with_drawscore",
    "centipawn_2019",
    "centipawn_2018",
    "win_percentage",
    "Q",
    "W-L",
    "WDL_mu",
}


class Lc0TelemetryAdapter(BaseUciTelemetryAdapter):
    engine = "lc0"
    nodes_unit = "count"
    default_multipv_if_missing = 1

    def __init__(
        self,
        *,
        score_type: str,
        defer_completion_until_flush: bool = False,
        **kwargs: Any,
    ):
        if score_type not in _SCORE_TYPES:
            raise ValueError(f"unsupported LC0 ScoreType: {score_type}")
        self.score_type = score_type
        self.defer_completion_until_flush = defer_completion_until_flush
        self._pending_bestmove: tuple[dict[str, Any], int | float] | None = None
        super().__init__(**kwargs)

    def score_semantics(self, kind: str) -> tuple[str, str]:
        if kind == "mate":
            return "mate", "lc0.uci_mate"
        if kind != "cp":
            raise ValueError(f"unsupported LC0 score kind: {kind}")
        output_kind = "cp" if self.score_type.startswith("centipawn") else "scalar"
        return output_kind, f"lc0.uci_score.{self.score_type}"

    def wdl_semantics(self) -> str:
        return "lc0.uci_wdl"

    @staticmethod
    def _parse_defect_payload(comment: str, prefix: str) -> dict[str, Any]:
        raw = comment[len(prefix) :].strip()
        if not raw:
            raise TelemetryParseError(f"{prefix.strip()} payload is missing")
        try:
            payload = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise TelemetryParseError(f"malformed {prefix.strip()} JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise TelemetryParseError(f"{prefix.strip()} payload must be a JSON object")
        if payload.get("v") != 1:
            raise TelemetryParseError(f"unsupported {prefix.strip()} payload version: {payload.get('v')!r}")
        return payload

    def flush_completion(self, *, observed_ms: int | float) -> dict[str, Any]:
        if self._pending_bestmove is None:
            raise TelemetryParseError("no deferred LC0 bestmove is pending")
        parsed, received_ms = self._pending_bestmove
        parsed = dict(parsed)
        extra = dict(parsed.get("extra", {}))
        extra["received_observed_ms"] = received_ms
        parsed["extra"] = extra
        self._pending_bestmove = None
        return self._complete_event(parsed, observed_ms=observed_ms)

    def consume(self, line: str, *, observed_ms: int | float) -> list[dict[str, Any]]:
        if self.defer_completion_until_flush and line.startswith("bestmove "):
            if self._pending_bestmove is not None:
                raise TelemetryParseError("multiple deferred LC0 bestmove lines")
            self._pending_bestmove = (parse_bestmove_line(line), observed_ms)
            return []
        if line.startswith("info "):
            parsed = parse_info_line(line)
            comment = parsed.get("comment")
            if isinstance(comment, str):
                if comment.startswith("DEFECT_TELEMETRY_ITER "):
                    payload = self._parse_defect_payload(comment, "DEFECT_TELEMETRY_ITER ")
                    return [
                        self.stream.emit(
                            "native.event",
                            observed_ms=observed_ms,
                            payload={"native": {"schema": "lc0.defect.iter.v1", "data": payload}},
                        )
                    ]
                if comment.startswith("DEFECT_TELEMETRY_SUMMARY "):
                    payload = self._parse_defect_payload(comment, "DEFECT_TELEMETRY_SUMMARY ")
                    return [
                        self.stream.emit(
                            "native.event",
                            observed_ms=observed_ms,
                            payload={"native": {"schema": "lc0.defect.summary.v1", "data": payload}},
                        )
                    ]
        return super().consume(line, observed_ms=observed_ms)
