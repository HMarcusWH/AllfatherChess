"""Out-of-order UCI frames, bounds, ABA epochs and native score semantics."""
import unittest

from controller.strength.contracts import TurnKey,SearchIdentity,StrengthContractError,digest
from controller.strength.epoch import IncumbentEpochTracker
from controller.strength.root_frames import PairFrames,parse_info


def identity():
    key=TurnKey("a"*32,7,"b"*64,"w","standard","c"*64,"d"*32)
    return SearchIdentity(key,"e"*40,"f"*64,"1"*64,"2"*64)


class EpochFramesTests(unittest.TestCase):
    def test_single_root_progress_and_aba_are_not_reused(self):
        t=IncumbentEpochTracker(identity())
        a=t.note_frame(parse_info("info depth 8 nodes 100 score cp 20 pv e2e4"),token=7,observed_ns=100)
        self.assertEqual((a.root_epoch,a.progress_seq),(1,1))
        b=t.note_frame(parse_info("info depth 10 nodes 500 score cp 31 pv d2d4"),token=7,observed_ns=200)
        self.assertEqual((b.root_epoch,b.progress_seq),(2,2))
        c=t.note_frame(parse_info("info depth 12 nodes 1000 score cp 24 pv e2e4"),token=7,observed_ns=300)
        self.assertEqual((c.root_epoch,c.progress_seq),(3,3))
        d=t.note_frame(parse_info("info depth 13 nodes 1100 score cp 26 pv e2e4"),token=7,observed_ns=400)
        self.assertEqual((d.root_epoch,d.progress_seq),(3,4))
        self.assertFalse(t.is_current(epoch=1,move="e2e4"))
        self.assertTrue(t.is_current(epoch=3,move="e2e4",progress_seq=4))
        terminal=t.terminal("e2e4",token=7,observed_ns=500)
        self.assertEqual((terminal.root_epoch,terminal.progress_seq),(3,5))
        self.assertIsNone(terminal.exact_score_mate)
        with self.assertRaises(StrengthContractError):
            t.terminal("d2d4",token=7,observed_ns=600)

    def test_terminal_not_equal_last_pv_increments_epoch(self):
        t=IncumbentEpochTracker(identity())
        t.note_frame(parse_info("info depth 12 nodes 600 score cp 20 pv e2e4"),token=7,observed_ns=10)
        c=t.terminal("d2d4",token=7,observed_ns=20)
        self.assertEqual(c.root_epoch,2)
        self.assertIsNone(c.exact_score_cp)

    def test_stale_generation_refused(self):
        t=IncumbentEpochTracker(identity())
        with self.assertRaises(StrengthContractError):
            t.note_frame(parse_info("info depth 8 nodes 50 pv e2e4"),token=8,observed_ns=1)
        with self.assertRaises(StrengthContractError):
            t.terminal("e2e4",token=8,observed_ns=2)

    def test_mate_bound_and_missing_score_are_distinct(self):
        mate=parse_info("info depth 14 nodes 300 score mate -3 pv e2e4")
        self.assertIsNone(mate.cp)
        self.assertEqual(mate.mate,-3)
        self.assertTrue(mate.exact)
        bound=parse_info("info depth 18 nodes 1000 score cp 75 lowerbound pv e2e4")
        self.assertFalse(bound.exact)
        self.assertIsNone(bound.cp)
        self.assertEqual(bound.bound,"lowerbound")
        no_score=parse_info("info depth 7 nodes 300 pv e2e4")
        self.assertIsNone(no_score.cp)
        self.assertFalse(no_score.exact)

    def test_same_depth_multipv_pair_requires_complete_exact_frame(self):
        p=PairFrames()
        self.assertIsNone(p.observe("info depth 10 multipv 1 nodes 100 score cp 40 pv e2e4"))
        frame=p.observe("info depth 10 multipv 2 nodes 180 score cp 20 pv d2d4")
        self.assertEqual([x.move for x in frame],["e2e4","d2d4"])
        self.assertIsNone(p.observe("info depth 11 multipv 1 nodes 230 score cp 44 pv e2e4"))
        self.assertIsNone(p.observe("info depth 11 multipv 2 nodes 240 score cp 25 upperbound pv d2d4"))
        self.assertIsNone(p.observe("info depth 12 multipv 1 nodes 300 score cp 48 pv e2e4"))
        self.assertIsNone(p.observe("info depth 12 multipv 2 nodes 330 score mate 3 pv d2d4"))
        self.assertFalse(parse_info("info string Hello there"))
        self.assertIsNone(parse_info("info depth 10 multipv 1 score cp NaN pv e2e4"))
        self.assertIsNone(parse_info("info depth 10 multipv 1 score cp 40 pv z9z9"))

    def test_root_restriction_denies_out_of_set(self):
        t=IncumbentEpochTracker(identity(),("e2e4",))
        self.assertIsNone(t.note_frame(parse_info("info depth 10 nodes 55 pv d2d4"),token=7,observed_ns=1))
        with self.assertRaises(StrengthContractError):
            t.terminal("d2d4",token=7,observed_ns=2)


if __name__=="__main__":
    unittest.main()
