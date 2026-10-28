"""S3-A: source-only nomination and pair contracts, destructive fixtures."""
from __future__ import annotations
from dataclasses import replace
from copy import deepcopy
import hashlib
import json
import unittest

from controller.strength.candidates import (
    CandidateProposalV1, make_pair, reconstruct_proposal)
from controller.strength.contracts import (
    TurnKey, SearchIdentity, IncumbentCheckpoint, StrengthContractError, digest)

CMD="position startpos moves e2e4"
LEGAL=("e7e5","c7c5","e7e6","c7c6","g8f6")
ROOTS=("e7e5","c7c5","e7e6")
H=lambda c:c*64


def fixture(*, family="reckless", external=(), candidate="c7c5"):
    turn=TurnKey(session_id="1"*32,generation=7,
                 history_sha256=hashlib.sha256(CMD.encode()).hexdigest(),
                 side_to_move="b",variant="standard",
                 restriction_sha256=digest({"searchmoves":list(external)}),
                 search_nonce="2"*32)
    anchor=SearchIdentity(turn,"3"*40,H("a"),H("b"),H("c"))
    proposer=SearchIdentity(turn,"3"*40,H("a"),H("d"),H("e"))
    incumbent=IncumbentCheckpoint(
        search_digest=anchor.search_digest,root_epoch=3,progress_seq=9,
        move="e7e5",depth=13,nodes=5601,exact_score_cp=22,
        exact_score_mate=None,observed_ns=100)
    return CandidateProposalV1(
        anchor_search=anchor,proposer_search=proposer,
        incumbent=incumbent,position_command=CMD,
        allowed_legal_roots=LEGAL,external_searchmoves=tuple(external),
        family=family,move=candidate,
        native_work_semantics=family+".uci_nodes",
        native_work_count=4096,grant_digest=H("f"),
        settlement_sha256=H("9"),observed_ns=110,
        proposer_network_sha256=H("8") if family=="lc0" else None)


class S3CandidateContracts(unittest.TestCase):
    def test_nomination_is_never_a_quality_or_outward_authority(self):
        for family in ("lc0","reckless"):
            with self.subTest(family=family):
                proposal=fixture(family=family)
                row=proposal.as_dict()
                self.assertEqual(reconstruct_proposal(json.loads(json.dumps(row))),proposal)
                self.assertEqual(row["authority"],{
                    "outward_move":False,"quality":False,"work_dispatch":False})
                self.assertEqual(proposal.proposal_digest,digest(row))
                pair=make_pair(proposal,proposal.incumbent)
                self.assertEqual(set(pair.ordered_roots),{"e7e5","c7c5"})
                self.assertEqual(len(pair.ordered_roots),2)
                self.assertEqual(pair.as_dict()["proposal_digest"],proposal.proposal_digest)
                self.assertEqual(pair.as_dict()["tt_policy"],"not_dispatched_source_only")

    def test_incumbent_epoch_changes_including_aba_invalidate_pair(self):
        p=fixture()
        with self.assertRaises(StrengthContractError):
            make_pair(p,replace(p.incumbent,root_epoch=5))
        with self.assertRaises(StrengthContractError):
            make_pair(p,replace(p.incumbent,move="e7e6",root_epoch=3))
        progressed=replace(p.incumbent,progress_seq=10)
        self.assertEqual(make_pair(p,progressed).current_incumbent,progressed)
        with self.assertRaises(StrengthContractError):
            make_pair(p,replace(p.incumbent,progress_seq=8))
        pair=make_pair(p,p.incumbent)
        with self.assertRaises(StrengthContractError):
            replace(pair,ordered_roots=("c7c5","c7c5"))

    def test_nomination_rejects_self_vote_unsupported_roots_or_restrictions(self):
        base=fixture()
        variations=[
            {"move":"e7e5"},{"move":"a7a5"},
            {"family":"stockfish","native_work_semantics":"stockfish.uci_nodes"},
            {"native_work_count":True},{"native_work_count":-1},
            {"native_work_semantics":"lc0.q"},
            {"proposer_network_sha256":H("1")},
            {"position_command":"position startpos"},
            {"observed_ns":90},
            {"grant_digest":"0"*63+"X"},
            {"allowed_legal_roots":("e7e5","c7c5","c7c5")},
            {"allowed_legal_roots":("c7c5",)},
            {"external_searchmoves":("e7e5","c7c5")},
            {"proposer_search":replace(base.proposer_search,source_commit="4"*40)},
            {"incumbent":replace(base.incumbent,search_digest=H("0"))},
        ]
        for change in variations:
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                replace(base,**change)
        self.assertEqual(fixture(external=ROOTS).move,"c7c5")
        with self.assertRaises(StrengthContractError):
            fixture(external=("e7e5",),candidate="c7c5")

    def test_forged_fields_cannot_roundtrip(self):
        row=fixture().as_dict()
        cases=[]
        for name,value in [
            ("authority",{"outward_move":True,"quality":False,"work_dispatch":False}),
            ("position_command","position startpos"),
            ("family","stockfish"),
            ("move","e7e5"),
            ("observed_ns",0),
            ("native_work_count",True),
            ("native_work_semantics","reckless.cp"),
        ]:
            altered=deepcopy(row)
            altered[name]=value
            cases.append(altered)
        for nested,field,value in [
            ("anchor_search","source_commit","9"*40),
            ("proposer_search","profile_sha256",H("f")),
            ("incumbent","root_epoch",True),
        ]:
            altered=deepcopy(row)
            altered[nested][field]=value
            cases.append(altered)
        altered=deepcopy(row);altered["unknown_authority"]=True;cases.append(altered)
        altered=deepcopy(row);altered.pop("settlement_sha256");cases.append(altered)
        for altered in cases:
            with self.subTest(change=altered),self.assertRaises((ValueError,TypeError,KeyError)):
                reconstruct_proposal(altered)

if __name__=="__main__":
    unittest.main()
