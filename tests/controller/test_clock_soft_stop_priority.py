"""Soft STOP must start shadow teardown without delaying anchor authority."""
from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from controller.uci_frontend import ShellState,UciFrontend


class ClockSoftStopPriority(unittest.TestCase):
    def fixture(self):
        shell=object.__new__(UciFrontend)
        shell._state_lock=threading.RLock()
        shell._state=ShellState.SEARCHING
        shell._active_generation=17
        clock=SimpleNamespace(
            finished=threading.Event(),work_closed=threading.Event(),
            dispatched=threading.Event(),
            plan=SimpleNamespace(hard_deadline=time.monotonic()+2),
            block_authority=lambda: True,
        )
        clock.dispatched.set()
        shell._clock_search=clock
        operations=[]
        shell.runtime=mock.Mock()
        shell.runtime.stop_anchor_for.side_effect=lambda *_args,**_kwargs: operations.append("anchor")
        shell._shadow_cancel=lambda *_args,**_kwargs: operations.append("shadow")
        return shell,clock,operations

    def test_soft_cutoff_schedules_shadow_stop_before_bounded_anchor_stop(self):
        shell,clock,operations=self.fixture()
        shell._clock_stop(17)
        self.assertEqual(operations,["shadow","anchor"])
        self.assertTrue(clock.work_closed.is_set())
        shell.runtime.stop_anchor_for.assert_called_once()
        self.assertGreater(shell.runtime.stop_anchor_for.call_args.kwargs["timeout"],0)

    def test_user_stop_keeps_anchor_authority_first(self):
        shell,clock,operations=self.fixture()
        shell._clock_stop(17,authority_invalidating=True)
        self.assertEqual(operations,["anchor","shadow"])

    def test_stale_generation_has_no_stop_or_impact(self):
        shell,clock,operations=self.fixture()
        shell._clock_stop(16)
        self.assertEqual(operations,[])
        self.assertFalse(clock.work_closed.is_set())

    def test_finished_clock_does_not_reissue_stops(self):
        shell,clock,operations=self.fixture()
        clock.finished.set()
        shell._clock_stop(17)
        self.assertEqual(operations,[])


if __name__=="__main__":
    unittest.main()
