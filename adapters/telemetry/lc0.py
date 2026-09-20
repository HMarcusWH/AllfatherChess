"""LC0 UCI and defect telemetry v1 adapter."""

from __future__ import annotations

import json
import math
from typing import Any

from .uci import BaseUciTelemetryAdapter, TelemetryParseError, parse_info_line


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

    def __init__(self, *, score_type: str, **kwargs: Any):
        if score_type not in _SCORE_TYPES:
            raise ValueError(f"unsupported LC0 ScoreType: {score_type}")
        self.score_type = score_type
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
    def _reject_nonfinite_native(value: Any, *, label: str = "payload") -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise TelemetryParseError(f"{label} contains a non-finite number")
        if isinstance(value, dict):
            for key, child in value.items():
                Lc0TelemetryAdapter._reject_nonfinite_native(
                    child, label=f"{label}.{key}"
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                Lc0TelemetryAdapter._reject_nonfinite_native(
                    child, label=f"{label}[{index}]"
                )

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
        Lc0TelemetryAdapter._reject_nonfinite_native(payload)
        version = payload.get("v")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise TelemetryParseError(
                f"unsupported {prefix.strip()} payload version: {version!r}"
            )
        return payload


    def consume(self, line: str, *, observed_ms: int | float) -> list[dict[str, Any]]:
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
