#!/usr/bin/env python3
from __future__ import annotations
import copy, unittest, sys, subprocess, tempfile, json, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.engine_opt.promotion_gate import PromotionGateError, evaluate_promotion_gate, verify_promotion_git_history

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
            'local1_g3':{'validated_games':28,'g3_authority_qualified':True,'g3_policy_sha256':'1'*64},
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
        report=evaluate_promotion_gate(aggregate=aggregate(),host_binding=binding(),catalog=catalog(),promotion_surface={'authorized_profile_change':True,'parent_sha':PARENT,'g3_b4_contract_verified':True,'g3_b4_policy_sha256':'1'*64},current_head=HEAD)
        self.assertTrue(report['passed'])
        self.assertEqual(report['disposition'],'CANONICAL_B4_PROMOTION_QUALIFIED')
        self.assertTrue(report['claim_boundary']['canonical_b4_promotion'])
        self.assertFalse(report['claim_boundary']['runtime_profile_selection'])

    def test_unfrozen_promotion_head_fails_closed(self):
        bad=binding(); bad['current_domain_bound_qualification']['qualified_head']='e'*40
        with self.assertRaisesRegex(PromotionGateError,'preceding promotion head'):
            evaluate_promotion_gate(aggregate=aggregate(),host_binding=bad,catalog=catalog(),promotion_surface={'authorized_profile_change':True,'parent_sha':PARENT,'g3_b4_contract_verified':True,'g3_b4_policy_sha256':'1'*64},current_head=HEAD)

    def test_non_b4_aggregate_is_rejected(self):
        bad=aggregate(); bad['details']['lc0']['selected_profile']='b7-p8-c256k-warm64'
        with self.assertRaises(PromotionGateError):
            evaluate_promotion_gate(aggregate=bad,host_binding=binding(),catalog=catalog(),promotion_surface={'authorized_profile_change':True,'parent_sha':PARENT,'g3_b4_contract_verified':True,'g3_b4_policy_sha256':'1'*64},current_head=HEAD)


class GitHistoryIntegrationTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT).strip()

    def test_full_history_resolves_exact_parent_and_base(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.git(root,"init","-q")
            self.git(root,"config","user.name","Test")
            self.git(root,"config","user.email","test@example.invalid")
            (root/"sentinel").write_text("a")
            self.git(root,"add",".")
            self.git(root,"commit","-qm","one")
            base=self.git(root,"rev-parse","HEAD")
            (root/"sentinel").write_text("b")
            self.git(root,"commit","-qam","two")
            parent=self.git(root,"rev-parse","HEAD")
            (root/"sentinel").write_text("c")
            self.git(root,"commit","-qam","three")
            head=self.git(root,"rev-parse","HEAD")
            self.assertEqual(verify_promotion_git_history(root,base),
                {"head_sha":head,"parent_sha":parent,"base_sha":base})
            with self.assertRaises(PromotionGateError):
                verify_promotion_git_history(root,"f"*40)

    def test_shallow_parent_failure_produces_sealed_negative_report(self):
        with tempfile.TemporaryDirectory() as temp:
            src=Path(temp)/"source"
            src.mkdir()
            self.git(src,"init","-q")
            self.git(src,"config","user.name","Test")
            self.git(src,"config","user.email","test@example.invalid")
            (src/"sentinel").write_text("a")
            self.git(src,"add",".")
            self.git(src,"commit","-qm","one")
            base=self.git(src,"rev-parse","HEAD")
            (src/"sentinel").write_text("b")
            self.git(src,"commit","-qam","two")
            clone=Path(temp)/"shallow"
            subprocess.run(["git","clone","-q","--depth=1",src.resolve().as_uri(),str(clone)],check=True)
            with self.assertRaisesRegex(PromotionGateError,"required Git history/object unavailable"):
                verify_promotion_git_history(clone,base)
            for name in ("aggregate.json","host.json","catalog.json"):
                (clone/name).write_text("{}")
            report_path=clone/"report.json"
            process=subprocess.run(
                [sys.executable,str(ROOT/"scripts/qualify-engine-opt-v2-promotion-gate.py"),
                 "--aggregate",str(clone/"aggregate.json"),
                 "--host-binding",str(clone/"host.json"),
                 "--catalog",str(clone/"catalog.json"),
                 "--base-sha",base,"--repo-root",str(clone),
                 "--output",str(report_path)],
                capture_output=True,text=True)
            self.assertEqual(process.returncode,2,process.stdout+process.stderr)
            report=json.loads(report_path.read_text())
            self.assertEqual(report["disposition"],"BLOCKED_FAIL_CLOSED")
            self.assertEqual(report["failure"]["type"],"PromotionGateError")
            self.assertFalse(report["claim_boundary"]["canonical_b4_promotion"])
            digest=report.pop("content_sha256")
            self.assertEqual(digest,hashlib.sha256(
                json.dumps(report,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
            ).hexdigest())

if __name__=='__main__': unittest.main()
