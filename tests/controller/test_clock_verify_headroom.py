"""Deterministic clocked VERIFY admission: too-late stages never dispatch.

The frozen TimePlan, LOCAL-1 thresholds and nonclocked experiments are not
modified by these tests. The 3.923/5.243ms cases are the exact negative
remaining-soft windows observed in the failed real lifecycle run.
"""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from controller.shadow import ShadowRunCoordinator


class ClockVerifyHeadroom(unittest.TestCase):
    NOW = 100.0
    QUIESCE_MS = 250

    def fixture(self, remaining_ms: float, *, clocked: bool = True,
                work_open: bool = True):
        coordinator=object.__new__(ShadowRunCoordinator)
        coordinator._closed=False
        coordinator.runtime=mock.Mock()
        coordinator.runtime.config=SimpleNamespace(
            online_time=SimpleNamespace(quiesce_budget_ms=self.QUIESCE_MS),
            verification=object(),
        )
        coordinator._j9_parent_fallback=lambda _active: False
        clock=SimpleNamespace(
            plan=SimpleNamespace(
                soft_deadline=self.NOW+remaining_ms/1000.0,
                hard_deadline=self.NOW+(remaining_ms+320)/1000.0,
            ),
            work_open=lambda: work_open,
        )
        active=SimpleNamespace(
            context=SimpleNamespace(clock=clock if clocked else None),
            cancelled=False,
            anchor_completed=threading.Event(),
            run=mock.Mock(),
        )
        return coordinator,active

    def permitted(self, coordinator, active):
        with mock.patch("controller.shadow.time.monotonic", return_value=self.NOW):
            return coordinator._verify_clock_headroom_open(active)

    def test_failed_lc0_verify_dispatch_windows_are_withheld(self):
        for ms in (3.923, 5.243):
            with self.subTest(remaining_soft_ms=ms):
                coordinator,active=self.fixture(ms)
                self.assertFalse(self.permitted(coordinator,active))
                with mock.patch("controller.shadow.time.monotonic",return_value=self.NOW):
                    coordinator._execute_verification(active)
                coordinator.runtime.shadow_available.assert_not_called()
                active.run.note.assert_called_once()
                self.assertIn("VERIFY skipped",active.run.note.call_args.args[0])

    def test_qualified_quiesce_reserve_is_not_an_arbitrary_new_margin(self):
        for ms in (5.243, 100, 249, 250):
            with self.subTest(ms=ms):
                coordinator,active=self.fixture(ms)
                self.assertFalse(self.permitted(coordinator,active))
        coordinator,active=self.fixture(400)
        self.assertTrue(self.permitted(coordinator,active))
        coordinator.runtime.config.online_time.quiesce_budget_ms=450
        self.assertFalse(self.permitted(coordinator,active))

    def test_soft_work_closed_denies_even_with_headroom(self):
        coordinator,active=self.fixture(1000,work_open=False)
        self.assertFalse(self.permitted(coordinator,active))

    def test_nonclocked_verify_retains_legacy_admission(self):
        coordinator,active=self.fixture(0,clocked=False)
        self.assertTrue(self.permitted(coordinator,active))

    def test_missing_online_policy_fails_closed_in_clock_mode(self):
        coordinator,active=self.fixture(1000)
        coordinator.runtime.config.online_time=None
        self.assertFalse(self.permitted(coordinator,active))

    def test_too_late_stage_precedes_any_workgrant_or_engine_write(self):
        coordinator,active=self.fixture(3.923)
        verification=mock.Mock()
        verification.plan=SimpleNamespace(
            participants={"lc0":"lc0-shadow"},
        )
        verification.stream.return_value=object()
        verification.stages.return_value=()
        active.verification=verification
        coordinator.runtime.spec.return_value=SimpleNamespace(family="lc0")
        with mock.patch("controller.shadow.time.monotonic",return_value=self.NOW):
            self.assertFalse(coordinator._dispatch_verification_stage(active,"lc0"))
        verification.set_disposition.assert_called_once_with(
            "skipped","clock_headroom_insufficient_for_optional_verify")
        coordinator.runtime.configure_shadow_phase.assert_not_called()
        coordinator.runtime.start_shadow_search.assert_not_called()

    def test_partially_dispatched_verify_cannot_be_claimed_skipped_or_complete(self):
        coordinator,active=self.fixture(3.923)
        verification=mock.Mock()
        verification.stages.return_value=(object(),)
        coordinator._withhold_late_verification(verification)
        verification.set_disposition.assert_called_once_with(
            "incomplete","clock_headroom_insufficient_for_optional_verify")


if __name__ == "__main__":
    unittest.main()
