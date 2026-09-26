"""Externally visible AllfatherChess UCI frontend."""

from __future__ import annotations

import io
import os
import queue
import re
import select
import stat
import sys
import threading
import time
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
        self._online_atomic_write_limit: int | None = None
        if self.online_time is not None and not isinstance(self.output, io.StringIO):
            fd = self._output_fd()
            limit = None if fd is None else self._atomic_pipe_write_limit(fd)
            if limit is None:
                raise RuntimeError(
                    "ONLINE mode requires StringIO for in-process tests or a "
                    "POSIX pipe/FIFO output with PIPE_BUF atomic-write semantics"
                )
            self._online_atomic_write_limit = limit
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

    def _clocked_hybrid_authority_enabled(self) -> bool:
        authority = getattr(getattr(self.runtime, "config", None), "hybrid_authority", None)
        return bool(
            self.online_time is not None
            and authority is not None
            and authority.policy == "clocked_staged_preanchor_v1"
        )

    def _output_fd(self) -> int | None:
        try:
            fd = self.output.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError, ValueError):
            return None
        return fd if isinstance(fd, int) and fd >= 0 else None

    @staticmethod
    def _atomic_pipe_write_limit(fd: int) -> int | None:
        """Return the atomic write bound only for POSIX pipe/FIFO descriptors."""
        try:
            mode = os.fstat(fd).st_mode
            if not stat.S_ISFIFO(mode):
                return None
            bound = int(os.fpathconf(fd, "PC_PIPE_BUF"))
        except (OSError, ValueError, TypeError):
            return None
        return bound if bound > 0 else None

    def _try_write_online_line_once(self, line: str) -> bool:
        """Attempt one complete ONLINE line without ever blocking."""
        if not self._write_lock.acquire(blocking=False):
            return False
        try:
            if isinstance(self.output, io.StringIO):
                self.output.write(line + "\n")
                self.output.flush()
                return True

            fd = self._output_fd()
            limit = self._online_atomic_write_limit
            if fd is None or limit is None:
                return False

            payload = (line + "\n").encode("ascii", errors="strict")
            if len(payload) > limit:
                raise RuntimeError(
                    "deadline-safe UCI line exceeds the output pipe atomic-write bound"
                )
            was_blocking = os.get_blocking(fd)
            if was_blocking:
                os.set_blocking(fd, False)
            try:
                try:
                    written = os.write(fd, payload)
                except BlockingIOError:
                    return False
            finally:
                if was_blocking:
                    os.set_blocking(fd, True)

            if written != len(payload):
                # POSIX requires writes <= PIPE_BUF to a pipe/FIFO to be
                # all-or-nothing. Treat violation as a transport invariant
                # failure; unsupported short-write descriptors are rejected at
                # construction and never reach this path.
                raise RuntimeError(
                    "atomic pipe contract produced an impossible partial UCI line"
                )
            return True
        finally:
            self._write_lock.release()

    def _wait_online_output_capacity(self, clock: ClockSearch) -> None:
        remaining = max(0.0, clock.plan.hard_deadline - time.monotonic())
        if remaining <= 0:
            return
        fd = self._output_fd()
        timeout = min(0.01, remaining)
        if fd is None:
            time.sleep(min(0.002, timeout))
            return
        try:
            select.select([], [fd], [], timeout)
        except (OSError, ValueError):
            time.sleep(min(0.002, timeout))

    def _publish_online_bestmove(
        self,
        *,
        token: int,
        clock: ClockSearch,
        anchor_line: str,
        final_decision,
    ) -> tuple[str | None, object | None, str]:
        """Publish one terminal line without blocking stop or hard expiry."""
        del token  # token is carried by the caller's terminal-state checks.
        while True:
            hybrid = (
                final_decision is not None
                and final_decision.authority == "HYBRID"
            )
            outward_line = anchor_line
            if (
                hybrid
                and final_decision.emitted_move != final_decision.anchor_move
            ):
                outward_line = f"bestmove {final_decision.emitted_move}"

            status = clock.try_publish(
                line=outward_line,
                require_authority=hybrid,
                write_once=lambda: self._try_write_online_line_once(outward_line),
            )
            if status == "published":
                return outward_line, final_decision, status
            if status == "revoked":
                final_decision = revoke_final_decision_to_anchor(
                    final_decision,
                    reason="clock authority revoked before bytes crossed stdout",
                )
                continue
            if status == "would_block":
                self._wait_online_output_capacity(clock)
                continue
            return None, final_decision, status

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
            suppress = self._clocked_hybrid_authority_enabled()
        # G3 may emit a move the Stockfish anchor did not select. Do not attach
        # the anchor's score/PV to a different outward move.
        if suppress:
            return
        self._write(line)

    def _on_search_complete(self, token: int, line: str) -> None:
        if self.online_time is not None:
            with self._state_lock:
                clock = self._clock_search
                if (
                    self._state != ShellState.SEARCHING
                    or self._active_generation != token
                    or clock is None
                ):
                    return
                expired = time.monotonic() >= clock.plan.hard_deadline
            if expired:
                self._clock_fail(token, "anchor answered after the clock deadline")
                return

            final_decision = None
            if self.shadow is not None:
                try:
                    # Publish the physical anchor-completion boundary for every
                    # ONLINE profile immediately. Hybrid-enabled profiles may
                    # also return a bounded decision here; anchor-only profiles
                    # return None. Deferred telemetry completion is a separate
                    # replay barrier and must never be the only signal that the
                    # engine process itself is idle.
                    final_decision = self.shadow.note_anchor_complete(token, line)
                except Exception as exc:
                    self._diagnostic(
                        f"clocked decision boundary failed: {exc}"
                    )

            with self._state_lock:
                clock = self._clock_search
                if (
                    self._state != ShellState.SEARCHING
                    or self._active_generation != token
                    or clock is None
                ):
                    return
                clock.work_closed.set()

            outward_line, final_decision, publish_status = (
                self._publish_online_bestmove(
                    token=token,
                    clock=clock,
                    anchor_line=line,
                    final_decision=final_decision,
                )
            )
            if publish_status == "expired":
                self._clock_fail(
                    token,
                    "outward publication reached the clock hard deadline",
                )
                return
            if publish_status != "published" or outward_line is None:
                return

            # Retain the exact outward decision in bounded in-memory replay
            # state before advertising READY. This is the durable publication
            # handshake for bytes that have already crossed stdout.
            if self.shadow is not None:
                try:
                    self.shadow.note_anchor_published(
                        token,
                        final_decision=final_decision,
                    )
                except Exception as exc:
                    self._diagnostic(
                        f"post-output decision publication failed: {exc}"
                    )

            # UCI readiness follows successful stdout publication. Slower
            # procfs/resource/replay evidence work may continue independently.
            with self._state_lock:
                if (
                    self._state == ShellState.SEARCHING
                    and self._active_generation == token
                ):
                    self._active_generation = None
                    self._state = (
                        ShellState.READY
                        if self.runtime.healthy
                        else ShellState.UNHEALTHY
                    )

            if self.shadow is not None:
                try:
                    self.shadow.note_anchor_emitted(token)
                except Exception as exc:
                    self._diagnostic(
                        f"post-output resource sample failed: {exc}"
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
            if (
                self._active_generation != token
                or self._state != ShellState.SEARCHING
                or clock is None
                or clock.finished.is_set()
            ):
                return
            clock.work_closed.set()
            if authority_invalidating and not clock.block_authority():
                # Publication already won the terminal race.
                return
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
            if (
                self._state != ShellState.SEARCHING
                or self._active_generation != token
                or clock is None
            ):
                return

        # Runtime failure / hard expiry is one atomic terminal clock operation.
        # There is never an intermediate revoked-but-publishable fallback state.
        if not clock.fail(reason=reason, line="bestmove 0000"):
            return

        with self._state_lock:
            if self._active_generation == token:
                self._active_generation = None
                self._state = ShellState.UNHEALTHY
            clock.work_closed.set()

        self.runtime.fail_clock_search(token, reason)
        self._diagnostic(reason)
        self._write("bestmove 0000")
        if self.shadow is not None:
            threading.Thread(
                target=lambda: self._shadow_cancel(reason, token),
                name=f"allfather-clock-failure-{token}",
                daemon=True,
            ).start()

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
                # Revoke before waiting for frontend state. Only launch an
                # invalidating stop if revocation actually wins; a stop that
                # arrives after terminal publication must not rewrite replay.
                clock_hint = self._clock_search
                revoked = False
                if clock_hint is not None:
                    clock_hint.work_closed.set()
                    revoked = clock_hint.block_authority()

                with self._state_lock:
                    clock = self._clock_search
                    if clock is not None and clock is not clock_hint:
                        clock.work_closed.set()
                        revoked = clock.block_authority()
                    token = (
                        self._active_generation
                        if revoked
                        and clock is not None
                        and not clock.finished.is_set()
                        else None
                    )

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
