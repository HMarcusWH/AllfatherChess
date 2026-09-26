"""Externally visible AllfatherChess UCI frontend."""

from __future__ import annotations

import re
import sys
import threading
import time
import queue
from enum import Enum
from typing import TYPE_CHECKING, TextIO

from .runtime import BackendManager, RuntimeError
from .online_time import ClockSearch, OnlineTimeError, make_time_plan
from .decision import revoke_final_decision_to_anchor
from .budget import ResourceEnvelope
from common.search_request import parse_position_command, SearchRequestError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .shadow import ShadowRunCoordinator


_SETOPTION_RE = re.compile(r"^setoption\s+name\s+(.+?)(?:\s+value(?:\s+(.*))?)?$")


class ShellState(str, Enum):
    READY = "ready"
    SEARCHING = "searching"
    UNHEALTHY = "unhealthy"
    SHUTDOWN = "shutdown"


class UciFrontend:
    """One external UCI identity with Stockfish as deterministic fallback.

    Shadow workers can never write to this stream directly. In an explicitly
    qualified M14-C profile, an already-frozen hybrid proposal may replace the
    anchor move only through the separate bounded DecisionAuthorization gate.
    """

    online_time = None  # Legacy test doubles may construct the shell with __new__.

    def __init__(
        self,
        runtime: BackendManager,
        *,
        output: TextIO | None = None,
        shadow: "ShadowRunCoordinator | None" = None,
    ) -> None:
        self.runtime = runtime
        self.shadow = shadow
        self.output = sys.stdout if output is None else output
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._state = ShellState.READY if runtime.healthy else ShellState.UNHEALTHY
        self._generation = 0
        self._active_generation: int | None = None
        self.online_time = getattr(getattr(runtime, "config", None), "online_time", None)
        self._clock_search: ClockSearch | None = None
        self._receipt_monotonic: float | None = None
        self._receipt_cpu_ns: int | None = None
        self.runtime.set_failure_handler(self._runtime_failed)

    @property
    def state(self) -> ShellState:
        with self._state_lock:
            return self._state

    def _write(self, line: str) -> None:
        with self._write_lock:
            self.output.write(line + "\n")
            self.output.flush()

    def _diagnostic(self, message: str) -> None:
        sanitized = " ".join(str(message).splitlines())
        self._write(f"info string Allfather {sanitized}")

    def _runtime_failed(self, message: str, token: int | None) -> None:
        if self.online_time is not None:
            with self._state_lock:
                active = self._active_generation
            if active is not None and (token is None or token == active):
                self._clock_fail(active, f"runtime failure: {message}")
                return
        with self._state_lock:
            if self._state == ShellState.SHUTDOWN:
                return
            searching = self._state == ShellState.SEARCHING
            active = self._active_generation
            if token is not None and searching and active is not None and token != active:
                return
            self._state = ShellState.UNHEALTHY
            self._active_generation = None

        # Avoid unsolicited UCI output while idle. An unhealthy idle runtime
        # reports its stored reason on the next readiness request. During an
        # active search we must unblock the GUI immediately and fail closed.
        if searching:
            self._shadow_cancel("anchor_failure", active)
            self._diagnostic(f"runtime failure: {message}")
            self._write("bestmove 0000")
            try:
                if self.runtime.anchor.alive:
                    self.runtime.anchor.stop()
            except Exception:
                pass

    def _on_search_info(self, token: int, line: str) -> None:
        with self._state_lock:
            if self._state != ShellState.SEARCHING or self._active_generation != token:
                return
            self._write(line)

    def _on_search_complete(self, token: int, line: str) -> None:
        if self.online_time is not None:
            with self._state_lock:
                clock = self._clock_search
                if self._state != ShellState.SEARCHING or self._active_generation != token or clock is None:
                    return
                if time.monotonic() >= clock.plan.hard_deadline:
                    self._clock_fail(token, "anchor answered after the clock deadline")
                    return

            final_decision = None
            authority = self.runtime.config.hybrid_authority
            if (
                authority is not None
                and authority.policy == "clocked_staged_preanchor_v1"
                and self.shadow is not None
            ):
                try:
                    final_decision = self.shadow.note_anchor_complete(token, line)
                except Exception as exc:
                    self._diagnostic(f"clocked hybrid decision boundary failed: {exc}")

            with self._state_lock:
                clock = self._clock_search
                if self._state != ShellState.SEARCHING or self._active_generation != token or clock is None:
                    return
                if time.monotonic() >= clock.plan.hard_deadline:
                    if self.shadow is not None:
                        self.shadow.invalidate_final_decision(
                            token, "decision selection crossed the clock deadline"
                        )
                    self._clock_fail(token, "decision selection crossed the clock deadline")
                    return
                # Selection ran outside the frontend state lock. A user stop
                # can revoke authority while that pure check is running, so
                # revalidate immediately before deciding the outward line.
                if final_decision is not None and not clock.authority_open():
                    final_decision = revoke_final_decision_to_anchor(
                        final_decision,
                        reason="clock authority revoked before outward write",
                    )
                clock.work_closed.set()
                outward_line = line
                if (
                    final_decision is not None
                    and final_decision.authority == "HYBRID"
                    and final_decision.emitted_move != final_decision.anchor_move
                ):
                    outward_line = f"bestmove {final_decision.emitted_move}"
                self._write(outward_line)
                clock.finish(line=outward_line)
                self._active_generation = None
                self._state = ShellState.READY if self.runtime.healthy else ShellState.UNHEALTHY

            if self.shadow is not None:
                try:
                    self.shadow.note_anchor_emitted(
                        token,
                        final_decision=final_decision,
                    )
                except Exception as exc:
                    self._diagnostic(
                        f"post-output decision/resource publication failed: {exc}"
                    )
                threading.Thread(
                    target=lambda: self._shadow_cancel(
                        "clock_anchor_complete",
                        token,
                        authority_invalidating=False,
                    ),
                    name=f"allfather-clock-complete-{token}",
                    daemon=True,
                ).start()
            return
        final_decision = None
        if self.shadow is not None:
            try:
                final_decision = self.shadow.note_anchor_complete(token, line)
            except Exception as exc:  # pragma: no cover - fail closed to anchor
                self._diagnostic(f"shadow decision boundary failed: {exc}")

        with self._state_lock:
            if self._state != ShellState.SEARCHING or self._active_generation != token:
                return
            self._active_generation = None
            self._state = ShellState.READY if self.runtime.healthy else ShellState.UNHEALTHY

        # ANCHOR_FALLBACK preserves the exact anchor line byte-for-byte. If the
        # hybrid root differs, never retain the anchor's optional ponder move:
        # that continuation belongs to a different root.
        outward_line = line
        if (
            final_decision is not None
            and final_decision.authority == "HYBRID"
            and final_decision.emitted_move != final_decision.anchor_move
        ):
            outward_line = f"bestmove {final_decision.emitted_move}"
        self._write(outward_line)

        if self.shadow is not None:
            try:
                self.shadow.note_anchor_emitted(
                    token,
                    final_decision=final_decision,
                )
            except Exception as exc:  # pragma: no cover - measurement is non-authoritative
                self._diagnostic(f"anchor terminal resource sample failed: {exc}")

    def _shadow_cancel(
        self,
        reason: str,
        generation: int | None = None,
        *,
        authority_invalidating: bool = True,
    ) -> None:
        """Cancel shadow observation without ever waiting on it.

        Every call here is on an authority path -- the command loop or a
        runtime-failure handler -- and cancellation writes `stop` to each
        dispatched shadow's stdin, which can block on a full pipe. `detach`
        keeps that write off this thread entirely; the coordinator's quiesce
        barrier joins the detached writer before any state change, so a late
        `stop` can never reach the next generation.
        """
        if self.shadow is None:
            return
        try:
            self.shadow.cancel(
                generation,
                reason=reason,
                detach=True,
                authority_invalidating=authority_invalidating,
            )
        except Exception as exc:  # pragma: no cover - shadow control is non-authoritative
            self._diagnostic(f"shadow cancel failed: {exc}")

    def _shadow_quiesce(self) -> None:
        """Hard barrier before any state mutation.

        A prior shadow generation must never be able to observe the next
        position, so state synchronization waits for the previous run to drain.

        A worker that misses the drain deadline is recorded as a shadow failure
        by the coordinator, which removes it from synchronization and dispatch.
        The mutation that follows therefore never reaches a process still
        executing the previous generation.
        """
        if self.shadow is None:
            return
        try:
            if not self.shadow.quiesce():
                self._diagnostic(
                    "previous shadow generation did not drain; the affected shadow "
                    "workers are recorded as failed and excluded from this state change"
                )
        except Exception as exc:  # pragma: no cover - shadow control is non-authoritative
            if self.online_time is not None:
                for instance in self.runtime.shadow_instances:
                    self.runtime.record_shadow_failure(instance, "clock synchronization barrier failed")
                    self.runtime.process(instance).kill_now()
            self._diagnostic(f"shadow quiesce failed: {exc}")

    def _reject_while_searching(self, command: str) -> None:
        self._diagnostic(f"rejected {command!r} while anchor search is active")

    def _handle_setoption(self, command: str) -> None:
        with self._state_lock:
            if self._state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return
            if self._state != ShellState.READY:
                return

        match = _SETOPTION_RE.fullmatch(command)
        if match is None:
            self._diagnostic("ignored malformed setoption command")
            return
        name = match.group(1).strip()
        value = (match.group(2) or "").strip()

        # PR9 deliberately exposes no resource-routing knobs. Unknown GUI
        # options are ignored rather than leaked into one constituent backend.
        if name != "UCI_Chess960":
            return
        lowered = value.lower()
        if lowered not in {"true", "false"}:
            self._diagnostic("UCI_Chess960 expects true or false")
            return
        if self.online_time is not None and lowered == "true":
            self._diagnostic("ONLINE-1 supports standard chess only; Chess960 was not enabled")
            return
        self._shadow_quiesce()
        try:
            self.runtime.set_chess960(lowered == "true")
        except RuntimeError as exc:
            if self.state != ShellState.UNHEALTHY:
                self._runtime_failed(str(exc), None)

    def _handle_ready(self) -> None:
        if self.state == ShellState.UNHEALTHY:
            self._diagnostic(self.runtime.unhealthy_reason or "runtime is unhealthy")
            return
        try:
            self.runtime.ready_authority()
        except RuntimeError as exc:
            if self.state != ShellState.UNHEALTHY:
                self._runtime_failed(str(exc), None)
            return
        self._write("readyok")

    def _handle_go(self, command: str) -> None:
        if self.online_time is not None:
            self._handle_clock_go(command)
            return
        with self._state_lock:
            if self._state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return
            if self._state != ShellState.READY or not self.runtime.healthy:
                self._diagnostic("cannot start search while runtime is unhealthy")
                return
            self._generation += 1
            token = self._generation
            self._active_generation = token
            self._state = ShellState.SEARCHING

        prepared = False
        if self.shadow is not None:
            try:
                prepared = self.shadow.prepare_run(generation=token, go_command=command)
            except Exception as exc:  # pragma: no cover - shadow setup is non-authoritative
                self._diagnostic(f"shadow run preparation failed: {exc}")

        if prepared and self.shadow is not None:
            try:
                self.shadow.note_anchor_dispatch(token)
            except Exception as exc:  # pragma: no cover - measurement is non-authoritative
                self._diagnostic(f"anchor resource measurement setup failed: {exc}")

        try:
            # The outward anchor always starts first. Shadow qualification and
            # dispatch happen afterwards on a worker thread so no observational
            # work can delay the decision authority.
            self.runtime.start_anchor_search(
                command,
                token=token,
                on_info=self._on_search_info,
                on_complete=self._on_search_complete,
            )
        except RuntimeError as exc:
            if prepared and self.shadow is not None:
                try:
                    self.shadow.abort_run(token, reason="anchor_dispatch_failed")
                except Exception:  # pragma: no cover
                    pass
            with self._state_lock:
                already_failed = self._state == ShellState.UNHEALTHY
            if not already_failed:
                self._runtime_failed(f"search dispatch failed: {exc}", token)
            return

        if prepared and self.shadow is not None:
            try:
                self.shadow.start_shadow_work(token)
            except Exception as exc:  # pragma: no cover - shadow control is non-authoritative
                self._diagnostic(f"shadow dispatch failed: {exc}")

    def _handle_clock_go(self, command: str) -> None:
        received = time.monotonic() if self._receipt_monotonic is None else self._receipt_monotonic
        cpu_started = time.process_time_ns() if self._receipt_cpu_ns is None else self._receipt_cpu_ns
        with self._state_lock:
            if self._state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return
            if self._state != ShellState.READY or not self.runtime.healthy:
                self._diagnostic("cannot start search while runtime is unhealthy")
                return
            token = self._generation + 1
            try:
                position = parse_position_command(self.runtime.position_command or "position startpos",
                                                  variant="chess960" if self.runtime.chess960 else "standard")
                plan = make_time_plan(command=command, position=position, generation=token,
                                      settings=self.online_time,
                                      envelope=ResourceEnvelope.from_config(self.runtime.config.budget),
                                      received_monotonic=received, controller_cpu_started_ns=cpu_started)
            except (OnlineTimeError, SearchRequestError, ValueError) as exc:
                self._diagnostic(f"clock request rejected: {exc}")
                self._write("bestmove 0000")  # Protocol failure, not a legal fallback move.
                return
            self._generation = token
            self._active_generation = token
            self._state = ShellState.SEARCHING
            clock = ClockSearch(plan)
            self._clock_search = clock
        clock.start(lambda: self._clock_stop(token), lambda: self._clock_fail(token, "clock hard deadline exceeded"))
        prepared = False
        if self.shadow is not None:
            try:
                prepared = self.shadow.prepare_run(generation=token, go_command=command, clock=clock)
            except Exception as exc:
                self._diagnostic(f"clock observation setup unavailable: {exc}")
        if not clock.work_open():
            self._clock_fail(token, "clock preparation consumed the available search window")
            if prepared and self.shadow is not None:
                self.shadow.start_shadow_work(token)  # finalizes cancelled/incomplete evidence
            return
        try:
            clock.dispatched.set()
            self.runtime.start_anchor_search(plan.anchor_go_command, token=token,
                on_info=self._on_search_info, on_complete=self._on_search_complete,
                clock=clock, observe_online=prepared,
                on_observation_end=self._on_clock_observation_end)
        except RuntimeError as exc:
            self._clock_fail(token, f"clock dispatch failed: {exc}")
        finally:
            if prepared and self.shadow is not None:
                # Also run finalization when dispatch failed or the anchor
                # already answered; all dispatch sites consume work_open().
                self.shadow.start_shadow_work(token)

    def _clock_stop(self, token: int, *, authority_invalidating: bool = False) -> None:
        with self._state_lock:
            clock = self._clock_search
            if self._active_generation != token or self._state != ShellState.SEARCHING or clock is None:
                return
            clock.work_closed.set()
            if authority_invalidating:
                clock.block_authority()
        remaining = max(0, clock.plan.hard_deadline - time.monotonic())
        if remaining > 0 and clock.dispatched.is_set():
            self.runtime.stop_anchor_for(token, timeout=remaining)
        self._shadow_cancel(
            "clock_user_stop" if authority_invalidating else "clock_soft_stop",
            token,
            authority_invalidating=authority_invalidating,
        )

    def _clock_fail(self, token: int, reason: str) -> None:
        with self._state_lock:
            clock = self._clock_search
            if self._state != ShellState.SEARCHING or self._active_generation != token or clock is None:
                return
            self._active_generation = None
            self._state = ShellState.UNHEALTHY
            clock.work_closed.set()
            clock.block_authority()
            self._diagnostic(reason)
            self._write("bestmove 0000")
            clock.finish(line="bestmove 0000", failure=reason)
        # No inference of a legal move from an incomplete PV. A dead/stuck
        # anchor is an explicit failed game; supervisor recovery is ONLINE-2/4.
        self.runtime.fail_clock_search(token, reason)
        if self.shadow is not None:
            threading.Thread(target=lambda: self._shadow_cancel(reason, token),
                             name=f"allfather-clock-failure-{token}", daemon=True).start()

    def _on_clock_observation_end(self, token: int, line: str, lost: int) -> None:
        if self.shadow is not None:
            self.shadow.note_clock_observation_end(token, line, lost)

    def handle_command(self, raw: str) -> bool:
        command = raw.strip()
        if not command:
            return True

        if command == "uci":
            self._write("id name AllfatherChess")
            self._write("id author AllfatherChess Project")
            self._write("option name UCI_Chess960 type check default false")
            self._write("uciok")
            return True

        if command == "isready":
            self._handle_ready()
            return True

        if command.startswith("setoption "):
            self._handle_setoption(command)
            return True

        if command == "ucinewgame":
            if self.state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return True
            if self.state != ShellState.READY:
                return True
            self._shadow_quiesce()
            try:
                self.runtime.new_game()
            except RuntimeError as exc:
                if self.state != ShellState.UNHEALTHY:
                    self._runtime_failed(str(exc), None)
            return True

        if command.startswith("position "):
            if self.state == ShellState.SEARCHING:
                self._reject_while_searching(command)
                return True
            if self.state != ShellState.READY:
                return True
            if not (command.startswith("position startpos") or command.startswith("position fen ")):
                self._diagnostic("unsupported position command")
                return True
            self._shadow_quiesce()
            try:
                self.runtime.set_position(command)
            except RuntimeError as exc:
                if self.state != ShellState.UNHEALTHY:
                    self._runtime_failed(str(exc), None)
            return True

        if command == "go" or command.startswith("go "):
            self._handle_go(command)
            return True

        if command == "stop":
            if self.online_time is not None:
                with self._state_lock:
                    token = self._active_generation
                    clock = self._clock_search
                    if clock is not None:
                        clock.work_closed.set()
                        clock.block_authority()
                if token is not None:
                    threading.Thread(
                        target=lambda: self._clock_stop(
                            token, authority_invalidating=True
                        ),
                        name=f"allfather-clock-user-stop-{token}",
                        daemon=True,
                    ).start()
                return True
            if self.state == ShellState.SEARCHING:
                # AUTHORITY FIRST. Cancelling shadows ahead of this sent `stop`
                # to every dispatched observational process, so one blocked
                # shadow stdin meant `stop_anchor()` was never reached and the
                # anchor's already-computed `bestmove` was never requested. The
                # GUI's `stop` is a decision-authority command; observation is
                # torn down afterwards and off this thread.
                try:
                    self.runtime.stop_anchor()
                except RuntimeError as exc:
                    if self.state != ShellState.UNHEALTHY:
                        self._runtime_failed(str(exc), self._active_generation)
                self._shadow_cancel("stop", self._active_generation)
            return True

        if command == "ponderhit":
            if self.online_time is not None:
                self._diagnostic("pondering is disabled in ONLINE-1")
                return True
            if self.state == ShellState.SEARCHING:
                try:
                    self.runtime.ponderhit_anchor()
                except RuntimeError as exc:
                    if self.state != ShellState.UNHEALTHY:
                        self._runtime_failed(str(exc), self._active_generation)
            return True

        if command == "quit":
            with self._state_lock:
                self._state = ShellState.SHUTDOWN
                self._active_generation = None
            self._shutdown()
            return False

        # UCI permits engines to ignore commands/options they do not implement.
        return True

    def run(self, input_stream: TextIO | None = None) -> None:
        stream = sys.stdin if input_stream is None else input_stream
        try:
            if self.online_time is None:
                for raw in stream:
                    if not self.handle_command(raw):
                        return
            else:
                receipts: queue.Queue = queue.Queue(maxsize=256)
                done = threading.Event()
                def read_commands():
                    try:
                        for raw in stream:
                            receipt = (raw, time.monotonic(), time.process_time_ns())
                            while not done.is_set():
                                try:
                                    receipts.put(receipt, timeout=0.05)
                                    break
                                except queue.Full:
                                    continue
                            if done.is_set():
                                return
                    finally:
                        while not done.is_set():
                            try:
                                receipts.put(None, timeout=0.05)
                                break
                            except queue.Full:
                                continue
                threading.Thread(target=read_commands, name="allfather-clock-input", daemon=True).start()
                try:
                    while True:
                        receipt = receipts.get()
                        if receipt is None:
                            break
                        raw, self._receipt_monotonic, self._receipt_cpu_ns = receipt
                        try:
                            if not self.handle_command(raw):
                                return
                        finally:
                            self._receipt_monotonic = None
                            self._receipt_cpu_ns = None
                finally:
                    done.set()
        finally:
            if self.state != ShellState.SHUTDOWN:
                with self._state_lock:
                    self._state = ShellState.SHUTDOWN
                    self._active_generation = None
                self._shutdown()

    def _shutdown(self) -> None:
        """Close shadow work before the processes so no run is left orphaned."""
        if self._clock_search is not None:
            self._clock_search.finish(failure="shutdown")
        if self.shadow is not None:
            try:
                self.shadow.close()
            except Exception as exc:  # pragma: no cover - shutdown is best effort
                self._diagnostic(f"shadow shutdown failed: {exc}")
        self.runtime.close()
