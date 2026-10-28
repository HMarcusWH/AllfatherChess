"""S3-B real-native proposal data contracts, no foreign score promotion."""
from __future__ import annotations
from dataclasses import replace
import unittest
from controller.strength.contracts import SearchIdentity,StrengthContractError
from controller.strength.proposer import (
    ProposerRequestV1,reconstruct_proposer,ProposerCollector)
from tools.strength_lab.s3a_rules import legal_roots
from tests.strength_s3a.test_candidates import fixture,CMD

SHA=lambda c:c*64
GOOD="info depth 11 seldepth 12 multipv 1 score cp 12 nodes 4500 time 35 pv c7c5 g1f3"


def make_request(family="reckless"):
    base=fixture(family=family)
    base=replace(base,allowed_legal_roots=legal_roots(CMD))
    return ProposerRequestV1(
        base.anchor_search,base.proposer_search,base.incumbent,
        CMD,base.allowed_legal_roots,(),family,
        base.proposer_search.binary_sha256,
        SHA("8") if family=="lc0" else None,
        4096 if family=="reckless" else 128,
        "7"*32)


class ProposalEvidence(unittest.TestCase):
    def test_real_reckless_terminal_non_incumbent(self):
        req=make_request()
        d=reconstruct_proposer(
            req,[{"line":GOOD,"ns":110}],"bestmove c7c5",
            terminal_ns=120,grant_digest=SHA("a"),
            settlement_sha256=SHA("b"))
        self.assertEqual(d["status"],"PROPOSED")
        self.assertEqual(d["proposal"]["move"],"c7c5")
        self.assertFalse(d["move_authorized"])
        self.assertEqual(d["proposal"]["native_work_semantics"],"reckless.uci_nodes")

    def test_lc0_can_nominate_nonincumbent_pv_when_terminal_is_incumbent(self):
        req=make_request("lc0")
        d=reconstruct_proposer(
            req,[{"line":GOOD,"ns":110}],"bestmove e7e5",
            terminal_ns=120,grant_digest=SHA("a"),
            settlement_sha256=SHA("b"))
        self.assertEqual(d["proposal"]["move"],"c7c5")
        self.assertEqual(d["native_limit_overshot"],True)
        self.assertFalse(d["quality_qualified"])

    def test_no_non_incumbent_is_valid_zero_coverage(self):
        req=make_request()
        d=reconstruct_proposer(req,[{"line":GOOD,"ns":110}],"bestmove e7e5",
            terminal_ns=120,grant_digest=SHA("a"),settlement_sha256=SHA("b"))
        self.assertEqual(d["status"],"NO_ALTERNATIVE")
        self.assertIsNone(d["proposal"])

    def test_foreign_source_native_limits_and_terminal_denied(self):
        req=make_request()
        for alter in (
            {"native_limit_nodes":500},
            {"proposal_nonce":"no"},
            {"engine_sha256":SHA("a")},
            {"network_sha256":SHA("a")},
            {"external_roots":("a7a5",)},
        ):
            with self.subTest(alter=alter),self.assertRaises(StrengthContractError):
                replace(req,**alter)
        with self.assertRaises(StrengthContractError):
            reconstruct_proposer(req,[{"line":GOOD,"ns":110}],
                "bestmove h7h8q",terminal_ns=120,
                grant_digest=SHA("a"),settlement_sha256=SHA("b"))
        with self.assertRaises(StrengthContractError):
            reconstruct_proposer(req,[{"line":GOOD,"ns":120}],
                "bestmove e7e5",terminal_ns=100,
                grant_digest=SHA("a"),settlement_sha256=SHA("b"))

    def test_collector_one_terminal_and_no_stale_generation(self):
        req=make_request()
        collector=ProposerCollector(req,token=7)
        collector.on_info(8,GOOD)
        collector.on_info(7,GOOD)
        collector.on_complete(7,"bestmove c7c5")
        self.assertEqual(collector.wait(.1,grant_digest=SHA("a"),
                         settlement_sha256=SHA("b"))["status"],"PROPOSED")
        collector.on_complete(7,"bestmove c7c5")
        with self.assertRaises(StrengthContractError):
            collector.wait(.1,grant_digest=SHA("a"),settlement_sha256=SHA("b"))

if __name__=="__main__":unittest.main()
