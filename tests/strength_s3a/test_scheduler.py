"""S3-A: one-slot scheduler uses real BudgetLedger reservations, no engines."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import unittest

from controller.budget import BudgetLedger, ResourceEnvelope
from controller.strength.contracts import TurnKey, SearchIdentity, StrengthContractError, digest
from controller.strength.scheduler import (StrengthAuxScheduler,
                                           CostObservationV1, StrengthTaskV1)


def rig(*, action="RECKLESS_ONLY", allow_fixture=True, verification_fraction=.4):
    turn=TurnKey("1"*32,1,hashlib.sha256(b"position startpos").hexdigest(),
                 "w","standard",digest({"searchmoves":[]}),"2"*32)
    identity=SearchIdentity(turn,"3"*40,"4"*64,"5"*64,"6"*64)
    clock=lambda:1.0
    ledger=BudgetLedger(ResourceEnvelope(wall_ms=5000,cpu_ms=10000,
             verification_reserve_fraction=verification_fraction,
             controller_overhead_reserve_ms=500),clock=clock,started=1.0)
    scheduler=StrengthAuxScheduler(
        anchor_search=identity,ledger=ledger,root_epoch=2,
        host_domain_sha256="7"*64,total_memory_bytes=6144*1024*1024,
        anchor_memory_bytes=2*1024**3,
        stop_cleanup_wall_ms=150,allow_source_fixture=allow_fixture)
    proposer_role="PROPOSE_RECKLESS" if action!="LC0_ONLY" else "PROPOSE_LC0"
    def cost(role,cpu,wall,memory):
        return CostObservationV1(
            semantic_role=role,source_commit=identity.source_commit,
            profile_sha256=identity.profile_sha256,
            host_domain_sha256="7"*64,evidence_sha256="8"*64,
            native_limit=4096,native_semantics={
                "PROPOSE_LC0":"lc0.uci_nodes",
                "PROPOSE_RECKLESS":"reckless.uci_nodes",
                "COMPARE_PAIR":"stockfish.uci_nodes"}[role],
            reserved_cpu_ms=cpu,reserved_gpu_ms=0,
            wall_bound_ms=wall,memory_peak_bytes=memory,
            review_status="SOURCE_TEST_FIXTURE")
    proposer=cost(proposer_role,300,220,100*1024*1024)
    comparator=cost("COMPARE_PAIR",550,320,200*1024*1024)
    return scheduler,ledger,identity,proposer,comparator


class OneSlotTests(unittest.TestCase):
    def prepare(self, s, p, c, action="RECKLESS_ONLY",slack=1200):
        return s.prepare(action=action,proposer_cost=p,comparator_cost=c,
                         current_root_epoch=2,native_slack_wall_ms=slack)

    def test_comparator_reserved_first_and_only_one_auxiliary_active(self):
        s,ledger,identity,p,c=rig()
        self.assertEqual(self.prepare(s,p,c),"RECKLESS_ONLY")
        journal=ledger.journal()
        self.assertEqual([r["lane"] for r in journal if r["event"]=="reserve"],
                         ["strength-s3-compare","strength-s3-propose"])
        self.assertEqual(ledger.snapshot()["open_reservations"],2)
        with self.assertRaises(StrengthContractError):
            s.begin_comparator(turn=identity.turn,epoch=2)
        t=s.begin_proposer(turn=identity.turn,epoch=2)
        self.assertEqual(t.semantic_role,"PROPOSE_RECKLESS")
        self.assertEqual(t.as_dict()["legacy_phase_mapping"],"EXPLORE")
        with self.assertRaises(StrengthContractError):
            s.begin_proposer(turn=identity.turn,epoch=2)
        s.settle_proposer(turn=identity.turn,epoch=2,actual_cpu_ms=190.,succeeded=True)
        s.begin_comparator(turn=identity.turn,epoch=2)
        s.settle_comparator(turn=identity.turn,epoch=2,actual_cpu_ms=420.,succeeded=True)
        self.assertEqual(s.state,"DONE")
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        self.assertTrue(ledger.within_envelope())
        self.assertEqual(s.report()["authority"]["outward_move"] if "outward_move" in s.report()["authority"]
                         else s.report()["authority"]["move"],False)
        self.assertFalse(s.report()["authority"]["work_dispatch"])
        with self.assertRaises(StrengthContractError):
            s.begin_proposer(turn=identity.turn,epoch=2)

    def test_no_host_cost_or_no_auxiliary_is_default(self):
        s,ledger,identity,p,c=rig()
        self.assertEqual(self.prepare(s,None,c),"NONE")
        self.assertEqual(s.reason,"NO_MEASURED_COST_PROFILE")
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        with self.assertRaises(StrengthContractError):
            self.prepare(s,p,c)
        s,ledger,identity,p,c=rig()
        self.assertEqual(self.prepare(s,p,c,action="NONE"),"NONE")
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        s,ledger,identity,p,c=rig(allow_fixture=False)
        self.assertEqual(self.prepare(s,p,c),"NONE")
        self.assertEqual(s.reason,"COST_NOT_INDEPENDENTLY_REVIEWED")

    def test_reserved_comparator_released_on_proposer_budget_denial(self):
        s,ledger,identity,p,c=rig()
        huge=replace(p,reserved_cpu_ms=9000)
        self.assertEqual(self.prepare(s,huge,c),"NONE")
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        journal=ledger.journal()
        self.assertEqual([x["event"] for x in journal],["reserve","release"])
        self.assertEqual([x["lane"] for x in journal],
                         ["strength-s3-compare","strength-s3-compare"])

    def test_no_comparator_feasibility_no_proposal(self):
        s,ledger,identity,p,c=rig()
        self.assertEqual(self.prepare(s,p,c,slack=100),"NONE")
        self.assertEqual(s.reason,"COMPARISON_NO_LONGER_FEASIBLE")
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        s,ledger,identity,p,c=rig()
        self.assertEqual(self.prepare(s,p,replace(c,memory_peak_bytes=6*1024**3)),"NONE")
        self.assertEqual(s.reason,"MEMORY_OUTSIDE_ENVELOPE")

    def test_stale_epoch_aba_and_wrong_search_nonce_fail_closed(self):
        s,ledger,identity,p,c=rig()
        self.prepare(s,p,c)
        bad=replace(identity.turn,search_nonce="9"*32)
        with self.assertRaises(StrengthContractError):
            s.begin_proposer(turn=bad,epoch=2)
        s.begin_proposer(turn=identity.turn,epoch=2)
        s.incumbent_changed(turn=identity.turn,new_epoch=3)
        self.assertEqual(s.state,"ABORTED")
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        self.assertEqual(ledger.snapshot()["lanes"]["strength-s3-propose"]["declared_fallback_settlements"],1)
        with self.assertRaises(StrengthContractError):
            s.begin_comparator(turn=identity.turn,epoch=3)
        self.assertEqual(s.report()["authority"]["move"],False)

    def test_helper_failure_spends_cost_and_prevents_retry(self):
        s,ledger,identity,p,c=rig()
        self.prepare(s,p,c)
        s.begin_proposer(turn=identity.turn,epoch=2)
        s.settle_proposer(turn=identity.turn,epoch=2,actual_cpu_ms=None,succeeded=False)
        self.assertEqual(s.state,"ABORTED")
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        self.assertEqual(ledger.snapshot()["lanes"]["strength-s3-propose"]["declared_fallback_settlements"],1)
        with self.assertRaises(StrengthContractError):
            s.begin_comparator(turn=identity.turn,epoch=2)
        self.assertTrue(s.report()["authority"]["strength"] is False)

    def test_overspend_is_settled_not_forgiven(self):
        s,ledger,identity,p,c=rig()
        self.prepare(s,p,c)
        s.begin_proposer(turn=identity.turn,epoch=2)
        s.settle_proposer(turn=identity.turn,epoch=2,actual_cpu_ms=301,succeeded=True)
        self.assertEqual(s.state,"ABORTED")
        self.assertEqual(ledger.snapshot()["lanes"]["strength-s3-propose"]["spent_cpu_ms"],301.)
        self.assertEqual(ledger.snapshot()["open_reservations"],0)

    def test_comparator_failure_is_not_replaced_or_refunded(self):
        s,ledger,identity,p,c=rig()
        self.prepare(s,p,c)
        s.begin_proposer(turn=identity.turn,epoch=2)
        s.settle_proposer(turn=identity.turn,epoch=2,actual_cpu_ms=180,succeeded=True)
        s.begin_comparator(turn=identity.turn,epoch=2)
        s.abort("ANCHOR_TERMINAL")
        self.assertEqual(s.state,"ABORTED")
        self.assertEqual(ledger.snapshot()["lanes"]["strength-s3-compare"]["declared_fallback_settlements"],1)
        self.assertEqual(ledger.snapshot()["open_reservations"],0)
        s.finish()
        self.assertEqual(s.report()["authority"]["comparison_quality"],False)

    def test_one_slot_concurrent_begin_is_serialized(self):
        s,ledger,identity,p,c=rig()
        self.prepare(s,p,c)
        def begin(_):
            try:
                s.begin_proposer(turn=identity.turn,epoch=2)
                return "STARTED"
            except StrengthContractError:
                return "DENIED"
        with ThreadPoolExecutor(max_workers=8) as pool:
            answers=list(pool.map(begin,range(8)))
        self.assertEqual(answers.count("STARTED"),1)
        self.assertEqual(answers.count("DENIED"),7)
        s.finish()
        self.assertEqual(ledger.snapshot()["open_reservations"],0)

    def test_typed_native_phase_and_cost_mismatch(self):
        s,ledger,identity,p,c=rig()
        with self.assertRaises(StrengthContractError):
            replace(p,native_semantics="lc0.uci_nodes")
        with self.assertRaises(StrengthContractError):
            replace(p,reserved_cpu_ms=float("nan"))
        with self.assertRaises(StrengthContractError):
            replace(p,reserved_gpu_ms=True)
        with self.assertRaises(StrengthContractError):
            replace(p,native_limit=True)
        with self.assertRaises(StrengthContractError):
            replace(p,review_status="SELF_REPORTED")
        with self.assertRaises(StrengthContractError):
            StrengthTaskV1(identity,"COMPARE_PAIR",0,c)

if __name__=="__main__":
    unittest.main()
