"""S2 native clock never converts Stockfish go into movetime."""
import dataclasses
import time
import unittest

from common.search_request import parse_position_command
from controller.strength.native_clock import (
    NativeClockSettings, make_native_plan, verify_native_plan,
)
from controller.strength.contracts import StrengthContractError

SETTINGS=NativeClockSettings()


class NativeClockTests(unittest.TestCase):
    def plan(self,command="go wtime 30000 btime 30000 winc 1000 binc 1000",
             position="position startpos moves e2e4"):
        return make_native_plan(command=command, position=parse_position_command(position),
                                generation=3,settings=SETTINGS,
                                received_monotonic=time.monotonic(),
                                controller_cpu_started_ns=time.process_time_ns())

    def test_native_clocks_and_side_not_converted(self):
        cmd="go wtime 18000 btime 13000 winc 800 binc 400 movestogo 12"
        p=self.plan(cmd)
        self.assertEqual(p.anchor_go_command,cmd)
        self.assertEqual(p.external_go_command,cmd)
        self.assertEqual(p.side_to_move,"b")
        self.assertEqual(p.hard_budget_ms,13000-SETTINGS.guard_ms)
        self.assertEqual(p.soft_budget_ms,p.hard_budget_ms-SETTINGS.stop_grace_ms-SETTINGS.output_margin_ms)
        self.assertEqual(verify_native_plan(p.as_dict()),[])
        self.assertFalse(p.as_dict()["authority"]["outward_move"])

    def test_increment_not_spent_for_loss_guard(self):
        cmd="go wtime 2000 btime 2000 winc 1000000 binc 1000000"
        p=self.plan(cmd)
        self.assertEqual(p.hard_budget_ms,1250)
        self.assertEqual(p.anchor_go_command,cmd)
        self.assertLessEqual(p.helper_advice_ms,200)

    def test_movetime_forwarded_unmodified(self):
        cmd="go movetime 3000 searchmoves e7e5 c7c5"
        p=self.plan(cmd)
        self.assertEqual(p.anchor_go_command,cmd)
        self.assertEqual(p.hard_budget_ms,2950)
        self.assertEqual(p.restriction,("e7e5","c7c5"))
        self.assertFalse(verify_native_plan(p.as_dict()))

    def test_unknown_mixed_ponder_or_unsupported_options_fail_closed(self):
        for cmd in ("go infinite","go ponder wtime 30000 btime 30000",
                    "go wtime 30000","go movetime 1000 nodes 1",
                    "go wtime 30000 btime 30000 wtime 20000",
                    "go wtime -1 btime 30000","go wtime 30000 btime 30000 searchmoves",
                    "go wtime 30000 btime 30000 searchmoves E7E5",
                    "go wtime 30000 btime 30000 searchmoves e7e5 e7e5",
                    "go movetime 2"):
            with self.subTest(cmd=cmd),self.assertRaises(StrengthContractError):
                self.plan(cmd)

    def test_native_plan_mutation_cannot_launder_v1(self):
        p=self.plan()
        doc=p.as_dict()
        for patch in ({"schema_version":1},
                      {"anchor_go_command":"go movetime 1000"},
                      {"hard_budget_ms":500},
                      {"restriction":["e7e5"]},
                      {"authority":{"timing":True,"outward_move":True}},
                      {"plan_id":"native-"+"f"*64}):
            with self.subTest(patch=patch):
                v={**doc,**patch}
                self.assertTrue(verify_native_plan(v))
        self.assertTrue(verify_native_plan({"schema_version":1,"policy":"clock_envelope_v1"}))

    def test_chess960_forbidden(self):
        with self.assertRaises(StrengthContractError):
            make_native_plan(command="go movetime 3000",
                position=parse_position_command("position startpos",variant="chess960"),
                generation=1,settings=SETTINGS,received_monotonic=time.monotonic(),
                controller_cpu_started_ns=time.process_time_ns())


if __name__=="__main__":
    unittest.main()
