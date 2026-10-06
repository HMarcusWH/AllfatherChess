#!/usr/bin/env python3
from __future__ import annotations
import copy, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.engine_opt.promotion_gate import PromotionGateError, evaluate_promotion_gate

HEAD='a'*40
PARENT='c'*40
DOMAIN='b'*64
BOUND_DOMAIN='d'*64

def aggregate():
    return {
        'source_commit':HEAD,'evidence_valid':True,'invalid_evidence':[],'profile_qualified':True,'passed':True,
        'qualification_disposition':'QUALIFIED_EXACT_HOST_ONLY','qualification_failures':[],
        'details':{
            'execution_domain':{'execution_domain_digest':DOMAIN},
            'lc0':{'selected_profile':'b4-p0-c256k-cold','native_work_policy':'lc0-node-stop-contract-v1'},
            'local1_g3':{'validated_games':28,'g3_authority_qualified':True},
        },
    }

def binding():
    return {
        'current_domain_bound_qualification':{
            'qualified_head':PARENT,'qualification_disposition':'QUALIFIED_EXACT_HOST_ONLY','execution_domain_digest':BOUND_DOMAIN,
        },
        'b4_candidate_promotion_seed':{'canonical_promotion_complete':True},
    }

def catalog():
    return {'qualification_snapshot':{'qualified_head':PARENT,'qualification_disposition':'QUALIFIED_EXACT_HOST_ONLY','execution_domain_digest':BOUND_DOMAIN}}

class PromotionGateTests(unittest.TestCase):
    def test_prior_head_binding_plus_exact_current_aggregate_closes_promotion(self):
        report=evaluate_promotion_gate(aggregate=aggregate(),host_binding=binding(),catalog=catalog(),promotion_surface={'authorized_profile_change':True,'parent_sha':PARENT},current_head=HEAD)
        self.assertTrue(report['passed'])
        self.assertEqual(report['disposition'],'CANONICAL_B4_PROMOTION_QUALIFIED')
        self.assertTrue(report['claim_boundary']['canonical_b4_promotion'])
        self.assertFalse(report['claim_boundary']['runtime_profile_selection'])

    def test_unfrozen_promotion_head_fails_closed(self):
        bad=binding(); bad['current_domain_bound_qualification']['qualified_head']='e'*40
        with self.assertRaisesRegex(PromotionGateError,'preceding promotion head'):
            evaluate_promotion_gate(aggregate=aggregate(),host_binding=bad,catalog=catalog(),promotion_surface={'authorized_profile_change':True,'parent_sha':PARENT},current_head=HEAD)

    def test_non_b4_aggregate_is_rejected(self):
        bad=aggregate(); bad['details']['lc0']['selected_profile']='b7-p8-c256k-warm64'
        with self.assertRaises(PromotionGateError):
            evaluate_promotion_gate(aggregate=bad,host_binding=binding(),catalog=catalog(),promotion_surface={'authorized_profile_change':True,'parent_sha':PARENT},current_head=HEAD)

if __name__=='__main__': unittest.main()
