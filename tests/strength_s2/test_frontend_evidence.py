"""S2 single-writer integration against a fake synchronous native Stockfish."""
from __future__ import annotations
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace

from controller.uci_frontend import UciFrontend,ShellState
from controller.strength.coordinator import StrengthCoordinator,S2Settings
from controller.strength.native_clock import NativeClockSettings
from controller.strength.integrity import strict_load,verify_flight


class _NativeRuntime:
    def __init__(self, *, bad_move=False):
        self.config=SimpleNamespace(
            strength=object(),online_time=NativeClockSettings(),budget=None,
            hybrid_authority=None)
        self.healthy=True
        self.chess960=False
        self.position_command="position startpos moves e2e4 e7e5"
        self.unhealthy_reason=None
        self.handler=None
        self.dispatched=[]
        self.bad_move=bad_move

    def set_failure_handler(self,cb):
        self.handler=cb

    def make_move_resource_plan(self,*args,**kwargs):
        raise AssertionError("S2 must never use legacy TimePlan resource scheduler")

    def start_anchor_search(self,command,*,token,on_info,on_complete,clock,observe_online,on_observation_end):
        self.dispatched.append(command)
        assert clock.plan.anchor_go_command==command
        assert observe_online is False
        on_info(token,"info depth 6 nodes 100 score cp 25 pv d2d4")
        on_info(token,"info depth 7 nodes 190 score cp 30 pv e2e4")
        on_info(token,"info depth 8 nodes 300 score cp 32 pv d2d4")
        on_info(token,"info depth 9 nodes 600 score cp 36 pv e2e4")
        on_complete(token,"bestmove 0000" if self.bad_move else "bestmove e2e4 ponder e7e5")

    def fail_clock_search(self,token,reason):
        self.healthy=False

    def close(self):
        pass


class FrontendS2Tests(unittest.TestCase):
    def make(self,path,bad_move=False):
        runtime=_NativeRuntime(bad_move=bad_move)
        shell=UciFrontend(runtime,output=io.StringIO())
        c=StrengthCoordinator(
            settings=S2Settings("a"*40,"b"*64,Path(path),4096),
            binary_sha256="c"*64,options_sha256="d"*64,
            session_id="e"*32)
        shell.strength=c
        return shell,runtime,c

    def test_native_exact_go_single_bestmove_and_independent_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell,runtime,c=self.make(tmp)
            command="go wtime 30000 btime 30000 winc 1000 binc 1000"
            self.assertTrue(shell.handle_command(command))
            self.assertEqual(runtime.dispatched,[command])
            output=shell.output.getvalue().splitlines()
            self.assertEqual([x for x in output if x.startswith("bestmove ")],
                             ["bestmove e2e4 ponder e7e5"])
            self.assertEqual(shell.state,ShellState.READY)
            self.assertGreaterEqual(len([x for x in output if x.startswith("info depth")]),3)
            shell._shutdown()
            found=list(Path(tmp).glob("*.json"))
            self.assertEqual(len(found),1)
            doc=strict_load(found[0])
            verdict=verify_flight(doc)
            self.assertEqual(verdict["status"],"PUBLISHED_ANCHOR")
            self.assertEqual(verdict["incumbent_epochs"],4)  # A->B->A->B->A
            self.assertFalse(verdict["qualified"])
            self.assertFalse(doc["replacement_authorized"])
            self.assertEqual(doc["events"][0]["data"]["position_command"],
                             "position startpos moves e2e4 e7e5")
            self.assertIn("search_nonce", doc["events"][0]["data"]["turn"])

    def test_invalid_anchor_terminal_is_null_failure_not_legal_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell,runtime,c=self.make(tmp,bad_move=True)
            shell.handle_command("go movetime 3000")
            self.assertFalse(runtime.healthy)
            self.assertEqual(shell.state,ShellState.UNHEALTHY)
            self.assertEqual([x for x in shell.output.getvalue().splitlines()
                              if x.startswith("bestmove ")],["bestmove 0000"])
            shell._shutdown()
            doc=strict_load(next(Path(tmp).glob("*.json")))
            self.assertEqual(verify_flight(doc)["status"],"ABORTED")

    def test_stale_info_does_not_change_incumbent(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell,runtime,c=self.make(tmp)
            shell.handle_command("go movetime 3000")
            before=c._tracker.root_epoch
            shell._on_search_info(1000,"info depth 99 nodes 999 score cp 10000 pv h2h4")
            self.assertEqual(c._tracker.root_epoch,before)
            shell._shutdown()

    def test_post_write_observer_failure_cannot_publish_second_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell,runtime,c=self.make(tmp)
            def crash_on_published(*,token,selection):
                raise RuntimeError("injected observer-only persistence fault")
            c.note_published=crash_on_published
            shell.handle_command("go movetime 3000")
            moves=[line for line in shell.output.getvalue().splitlines()
                   if line.startswith("bestmove ")]
            self.assertEqual(moves,["bestmove e2e4 ponder e7e5"])
            self.assertEqual(shell.state,ShellState.READY)
            self.assertTrue(runtime.healthy)
            shell._shutdown()
            doc=strict_load(next(Path(tmp).glob("*.json")))
            self.assertIn("post-write observer",doc["failure"])
            self.assertEqual(verify_flight(doc)["status"],"INVALID")

    def test_preterminal_pv_observer_fault_does_not_affect_native_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell,runtime,c=self.make(tmp)
            def crash_on_info(*,token,line,observed_ns=None):
                raise RuntimeError("injected PV observer failure")
            c.on_anchor_info=crash_on_info
            shell.handle_command("go movetime 3000")
            self.assertEqual([line for line in shell.output.getvalue().splitlines()
                              if line.startswith("bestmove ")],
                             ["bestmove e2e4 ponder e7e5"])
            self.assertTrue(runtime.healthy)
            shell._shutdown()
            doc=strict_load(next(Path(tmp).glob("*.json")))
            self.assertIn("PV observer",doc["failure"])
            self.assertEqual(verify_flight(doc)["status"],"INVALID")

    def test_corrupt_event_chain_and_forged_promotion_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell,runtime,c=self.make(tmp)
            shell.handle_command("go movetime 3000")
            shell._shutdown()
            doc=strict_load(next(Path(tmp).glob("*.json")))
            for update in (
                lambda r: r.update(qualified=True),
                lambda r: r["events"][1]["data"].update(move="h2h4"),
                lambda r: r["events"].append(deepcopy(r["events"][-1])),
                lambda r: r["events"][-1]["data"].update(chosen_line="bestmove d2d4"),
            ):
                with self.subTest(update=update):
                    broken=deepcopy(doc)
                    update(broken)
                    self.assertEqual(verify_flight(broken)["status"],"INVALID")


if __name__=="__main__":
    unittest.main()
