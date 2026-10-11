"""Deterministic, one-shot transport faults for offline ONLINE-4A.

Trigger selection depends on endpoint/ordinal, never wall-clock scheduling.
Each scenario is an experiment specification, not an authorization policy.
"""
from __future__ import annotations
import json
import threading
from pathlib import Path

TARGET = {
    "none": {"none"},
    "drop_before_commit": {"move_post"},
    "commit_then_close": {"move_post"},
    "commit_then_504": {"move_post"},
    "duplicate_move": {"move_post"},
    "delay_state": {"game_state"},
    "duplicate_state": {"game_state"},
    "stale_state": {"game_state"},
    "partial_chunk": {"game_state"},
    "replace_event_stream": {"event_stream"},
    "rate_limit_429": {"move_post"},
    "wrong_game": {"game_state"},
    "low_clock_close": {"move_post"},
    "terminate_bridge": {"move_post"},
    "duplicate_terminal": {"terminal_state"},
}
COLORS = {"white", "black"}
MODES = {"integration", "contract"}


def parse_scenario(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != {
        "id", "mode", "colors", "action", "target",
    }:
        raise ValueError("fault scenario fields must be exact")
    action = data["action"]
    target = data["target"]
    if action not in TARGET or not isinstance(target, dict) or set(target) != {"endpoint", "ordinal"}:
        raise ValueError("unknown fault action or target")
    if target["endpoint"] not in TARGET[action] or type(target["ordinal"]) is not int or target["ordinal"] < 1:
        raise ValueError("invalid fault endpoint or ordinal")
    if action == "none" and target["ordinal"] != 1:
        raise ValueError("normal scenario must have ordinal one")
    if not isinstance(data["id"], str) or not data["id"].startswith("R") or len(data["id"]) != 3:
        raise ValueError("invalid scenario id")
    if data["mode"] not in MODES or not isinstance(data["colors"], list) or not data["colors"] or len(set(data["colors"])) != len(data["colors"]) or not set(data["colors"]) <= COLORS:
        raise ValueError("invalid scenario colors or mode")
    return json.loads(json.dumps(data))


def load_scenarios(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"schema_version", "scenarios"} or data["schema_version"] != 1:
        raise ValueError("unsupported recovery scenario format")
    if not isinstance(data["scenarios"], list):
        raise ValueError("scenarios must be a list")
    rows = [parse_scenario(row) for row in data["scenarios"]]
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("duplicate fault scenario id")
    return {r["id"]: r for r in rows}


class FaultScheduler:
    def __init__(self, scenario: dict):
        self.scenario = parse_scenario(scenario)
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self.activations: list[dict] = []

    def hit(self, endpoint: str, *, ply: int, request_id: str | None = None) -> str | None:
        """Return the action once, recording the exact injection precondition."""
        with self.lock:
            ordinal = self.counts.get(endpoint, 0) + 1
            self.counts[endpoint] = ordinal
            if (self.scenario["action"] == "none"
                or self.activations
                or endpoint != self.scenario["target"]["endpoint"]
                or ordinal != self.scenario["target"]["ordinal"]):
                return None
            record = {"scenario_id": self.scenario["id"],
                      "action": self.scenario["action"],
                      "endpoint": endpoint, "endpoint_ordinal": ordinal,
                      "ply_at_activation": ply, "request_id": request_id}
            self.activations.append(record)
            return self.scenario["action"]

    def evidence(self) -> dict:
        with self.lock:
            return {"scenario": self.scenario,
                    "endpoint_counts": dict(self.counts),
                    "activations": list(self.activations)}
