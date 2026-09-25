"""Bounded, FIFO observation off the online anchor's sole stdout reader.

Only a prepared replay starts this worker. A blocked old replay prevents a new
prepared replay through the coordinator's generation barrier; unobserved anchor
fallbacks do not start more workers. Terminal events have a reserved slot.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable


class DeferredObserver:
    def __init__(self, *, observe: Callable[[int, str, float], None],
                 finished: Callable[[int, str, int], None], capacity: int = 4096) -> None:
        self._observe = observe
        self._finished = finished
        self._queue: queue.Queue[tuple[int, str, float]] = queue.Queue(maxsize=capacity)
        self._terminal: tuple[int, str, float] | None = None
        self._terminal_ready = threading.Event()
        self._abort = threading.Event()
        self._lost = 0
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="allfather-clock-observer", daemon=True)
        self._thread.start()

    def submit(self, token: int, line: str, observed: float) -> None:
        if self._abort.is_set():
            return
        if line.startswith("bestmove "):
            self._terminal = (token, line, observed)
            self._terminal_ready.set()
            return
        try:
            self._queue.put_nowait((token, line, observed))
        except queue.Full:
            self._lost += 1

    def abort(self) -> None:
        self._abort.set()

    def _run(self) -> None:
        try:
            while not self._abort.is_set():
                try:
                    item = self._queue.get(timeout=0.01)
                except queue.Empty:
                    if self._terminal_ready.is_set():
                        # Single producer: the terminal slot is published only
                        # after all preceding info submissions have returned.
                        terminal = self._terminal
                        if terminal is not None:
                            try:
                                self._observe(*terminal)
                            except Exception:
                                self._lost += 1
                            self._finished(terminal[0], terminal[1], self._lost)
                        return
                    continue
                try:
                    self._observe(*item)
                except Exception:
                    self._lost += 1
        finally:
            self.done.set()
