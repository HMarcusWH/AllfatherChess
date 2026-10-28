"""S3-B complete same-search same-depth MultiPV native comparison adversarial tests."""
from __future__ import annotations
from dataclasses import replace
import copy
import unittest
from controller.strength.comparison import (
    ComparisonRequestV1,reconstruct_comparison,ComparatorRoundCollector,legal_pv)
from controller.strength.contracts import SearchIdentity,StrengthContractError
from controller.strength.candidates import make_pair
from tests.strength_s3a.test_candidates import fixture,CMD
from tools.strength_lab.s3a_rules import legal_roots

SHA=lambda c:c*64
TERMINAL="bestmove c7c5"
ONE="info depth 11 seldepth 12 multipv 1 score cp 18 nodes 1631 time 12 pv e7e5 g1f3"
TWO="info depth 11 seldepth 13 multipv 2 score cp 35 nodes 3024 time 16 pv c7c5 g1f3"


def request():
    p=fixture()
    p=replace(p,allowed_legal_roots=legal_roots(CMD))
    pair=make_pair(p,p.incumbent)
    compare=SearchIdentity(p.turn,p.anchor_search.source_commit,
                           p.anchor_search.profile_sha256,
                           p.anchor_search.binary_sha256,SHA("f"))
    return ComparisonRequestV1(pair,compare,p.anchor_search.binary_sha256,
                               SHA("c"),SHA("d"),1,16384,"2"*32)


def entry(line,when):
    return {"line":line,"ns":when}


class ComparisonEvidence(unittest.TestCase):
    def test_frozen_exact_two_root_command_and_cp_replay(self):
        req=request()
        self.assertEqual(set(req.pair.ordered_roots),{"e7e5","c7c5"})
        self.assertEqual(req.go_command.split()[:4],["go","nodes","16384","searchmoves"])
        rows=[entry(ONE,100),entry(TWO,120)]
        doc=reconstruct_comparison(req,rows,TERMINAL,completed_ns=200)
        self.assertEqual(doc["disposition"],"COMPLETE_CP")
        self.assertEqual(doc["frame"]["depth"],11)
        self.assertEqual({doc["frame"]["rank1"]["root"],doc["frame"]["rank2"]["root"]},
                         {"e7e5","c7c5"})
        self.assertEqual(doc["request_sha256"],req.request_digest)
        self.assertFalse(doc["resource_qualified"])
        self.assertFalse(doc["replacement_authorized"])

    def test_latest_complete_not_mixed_across_depth_or_rank1(self):
        req=request()
        late="info depth 12 multipv 1 score cp 13 nodes 5100 time 21 pv e7e5"
        raw=[entry(ONE,100),entry(TWO,120),entry(late,150)]
        doc=reconstruct_comparison(req,raw,TERMINAL,completed_ns=200)
        self.assertEqual(doc["frame"]["depth"],11)
        partial=[entry(ONE,100),
                 entry("info depth 12 multipv 2 score cp 25 nodes 3000 time 15 pv c7c5",120)]
        self.assertEqual(reconstruct_comparison(
            req,partial,TERMINAL,completed_ns=200)["disposition"],"NO_COMPARISON")
        duplicate=[entry(ONE,100),entry(ONE,110),
                   entry(TWO,120)]
        self.assertEqual(reconstruct_comparison(
            req,duplicate,TERMINAL,completed_ns=200)["disposition"],"COMPLETE_CP")

    def test_bound_score_missing_or_legal_pv_failure_never_qualifies(self):
        req=request()
        cases=[
            ONE.replace("score cp 18","score cp 18 lowerbound"),
            ONE.replace("e7e5 g1f3","e7e5 e7e5"),
            ONE.replace("time 12","time -2"),
            ONE.replace("pv e7e5","pv a7a5"),
            ONE.replace("score cp 18","score mate 2"),
            ONE.replace("multipv 1","multipv 3"),
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                doc=reconstruct_comparison(req,[entry(bad,100),entry(TWO,120)],
                                           TERMINAL,completed_ns=200)
                self.assertEqual(doc["disposition"],"NO_COMPARISON")
        self.assertTrue(legal_pv(CMD,ONE,"e7e5"))
        self.assertFalse(legal_pv(CMD,ONE.replace("g1f3","e7e5"),"e7e5"))

    def test_mate_values_are_preserved_as_fallback_not_cp(self):
        req=request()
        x=ONE.replace("score cp 18","score mate 3")
        y=TWO.replace("score cp 35","score mate 1")
        doc=reconstruct_comparison(req,[entry(x,100),entry(y,120)],
                                   TERMINAL,completed_ns=200)
        self.assertEqual(doc["disposition"],"MATE_FALLBACK")
        self.assertFalse(doc["replacement_authorized"])

    def test_wrong_search_and_causality_denied(self):
        req=request()
        with self.assertRaises(StrengthContractError):
            replace(req,comparator_engine_sha256=SHA("a"))
        with self.assertRaises(StrengthContractError):
            replace(req,native_limit_nodes=True)
        with self.assertRaises(StrengthContractError):
            replace(req,round_nonce="x"*32)
        with self.assertRaises(StrengthContractError):
            reconstruct_comparison(req,[entry(ONE,201),entry(TWO,202)],
                                   TERMINAL,completed_ns=200)
        with self.assertRaises(StrengthContractError):
            reconstruct_comparison(req,[entry(ONE,100),entry(TWO,120)],
                                   "bestmove a7a5",completed_ns=200)
        with self.assertRaises(StrengthContractError):
            reconstruct_comparison(req,[entry(ONE,120),entry(TWO,110)],
                                   TERMINAL,completed_ns=200)

    def test_terminal_single_event_collector_replay(self):
        req=request()
        collector=ComparatorRoundCollector(req)
        collector.on_info(9,"info depth 100",)
        collector.on_info(1,ONE)
        collector.on_info(1,TWO)
        collector.on_complete(1,TERMINAL)
        result=collector.wait(.2)
        self.assertEqual(result["disposition"],"COMPLETE_CP")
        with self.assertRaises(StrengthContractError):
            collector.on_complete(1,TERMINAL)
            collector.wait(.2)

if __name__=="__main__":unittest.main()
