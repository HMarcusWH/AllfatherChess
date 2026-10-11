"""Per-connection queues and exclusive global event stream for the fake server.

Only one subscriber to the *global event stream* is active at a time.
Every reconnect gets a fresh gameFull snapshot, never a stale shared queue.
"""
from __future__ import annotations
import copy
import queue
import threading
from dataclasses import dataclass, field


@dataclass
class Subscriber:
    sid: int
    kind: str
    mailbox: queue.Queue = field(default_factory=queue.Queue)
    ended: threading.Event = field(default_factory=threading.Event)

    def get(self, timeout=0.5):
        if self.ended.is_set():
            return None
        try:
            return self.mailbox.get(timeout=timeout)
        except queue.Empty:
            return None


class StreamSessions:
    def __init__(self):
        self.lock = threading.RLock()
        self.next_sid = 0
        self.by_kind: dict[str, dict[int, Subscriber]] = {"event": {}, "game": {}}
        self.history: list[dict] = []

    def subscribe(self, kind: str, initial: dict | None = None) -> Subscriber:
        if kind not in self.by_kind:
            raise ValueError("unsupported stream kind")
        with self.lock:
            if kind == "event":
                for prior in self.by_kind[kind].values():
                    prior.ended.set()
                self.by_kind[kind].clear()
            self.next_sid += 1
            sub = Subscriber(self.next_sid, kind)
            self.by_kind[kind][sub.sid] = sub
            self.history.append({"event": "subscribe", "kind": kind, "sid": sub.sid})
            if initial is not None:
                sub.mailbox.put(copy.deepcopy(initial))
            return sub

    def publish(self, kind: str, payload: dict):
        with self.lock:
            for sub in list(self.by_kind[kind].values()):
                if not sub.ended.is_set():
                    sub.mailbox.put(copy.deepcopy(payload))
                    self.history.append({"event": "queued", "kind": kind,
                                         "sid": sub.sid, "type": payload.get("type")})

    def unsubscribe(self, sub: Subscriber):
        with self.lock:
            sub.ended.set()
            self.by_kind[sub.kind].pop(sub.sid, None)
            self.history.append({"event": "unsubscribe", "kind": sub.kind, "sid": sub.sid})

    def stop(self):
        with self.lock:
            for entries in self.by_kind.values():
                for sub in entries.values():
                    sub.ended.set()
                entries.clear()

    def snapshot(self):
        with self.lock:
            return list(self.history)
