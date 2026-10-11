"""Observational HTTP/game ledger; it is not a second authoritative board.

A server-side write does NOT prove the client consumed an acknowledgment.
The independent ONLINE-4A verifier reconstructs chess legality separately.
"""
from __future__ import annotations
import copy
import hashlib
import json
import threading
import time


def state_digest(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


class TransportTrace:
    def __init__(self):
        self.lock = threading.RLock()
        self.rows: list[dict] = []
        self.serial = 0

    def record(self, event: str, **fields) -> int:
        with self.lock:
            self.serial += 1
            self.rows.append(copy.deepcopy({"seq": self.serial, "event": event,
                                            "monotonic_ns": time.monotonic_ns(), **fields}))
            return self.serial

    def snapshot(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self.rows)
