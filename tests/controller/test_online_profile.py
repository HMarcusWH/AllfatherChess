#!/usr/bin/env python3
"""ONLINE-2 static profile, process-environment, and provenance tests."""
from __future__ import annotations
import copy,json,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from adapters.process import UciProcess
from controller.online_profile import OnlineProfileError,load_json,validate_policy,validate_runtime_config
from controller.runtime import BackendManager
from tests.controller.test_shadow_runtime import write_shadow_config
POLICY=ROOT/"qualification/online-cpu-reference.json"; CONFIG=ROOT/"config/allfather.online.cpu-reference.json"; LOCK=ROOT/"qualification/lc0-strength.lock.json"; STRENGTH=ROOT/"qualification/lc0-strength-profile.json"; VENDOR=ROOT/"vendor.lock.json"
class OnlineProfileStaticTests(unittest.TestCase):
  def setUp(self):
    self.policy=load_json(POLICY); self.config=load_json(CONFIG); self.lock=load_json(LOCK); self.strength=load_json(STRENGTH); self.vendor=load_json(VENDOR)
  def validate(self,config=None,policy=None):
    validate_runtime_config(config or self.config,self.lock,self.strength,policy or self.policy,self.vendor)
  def test_shipped_profile(self):
    validate_policy(self.policy,self.strength); self.validate()
    self.assertEqual(self.config["instances"]["lc0-shadow"]["options"]["Backend"],"blas"); self.assertNotIn("hybrid_authority",self.config)
  def test_native_reckless_rejected(self):
    p=copy.deepcopy(self.policy); p["builds"]["reckless"]["target_cpu"]="native"
    with self.assertRaises(OnlineProfileError): validate_policy(p,self.strength)
  def test_random_lc0_and_missing_weights_rejected(self):
    for key,value in (("Backend","random"),("WeightsFile","")):
      c=copy.deepcopy(self.config); c["instances"]["lc0-shadow"]["options"][key]=value
      with self.assertRaises(OnlineProfileError): self.validate(c)
  def test_missing_blas_environment_rejected(self):
    c=copy.deepcopy(self.config); c["instances"]["lc0-shadow"]["environment"].pop("OPENBLAS_NUM_THREADS")
    with self.assertRaises(OnlineProfileError): self.validate(c)
  def test_fallback_glob_rejected(self):
    c=copy.deepcopy(self.config); c["instances"]["lc0-shadow"]["fallback_glob"]="engines/lc0/**/lc0"
    with self.assertRaises(OnlineProfileError): self.validate(c)
  def test_authority_refine_verify_rejected(self):
    for key in ("hybrid_authority","refinement","verification"):
      c=copy.deepcopy(self.config); c[key]={"enabled":True}
      with self.assertRaises(OnlineProfileError): self.validate(c)
class RuntimeEnvironmentBindingTests(unittest.TestCase):
  def test_identity_binds_declared_environment(self):
    with tempfile.TemporaryDirectory() as tmp:
      path=write_shadow_config(Path(tmp)); doc=json.loads(path.read_text(encoding="utf-8")); doc["instances"]["lc0-shadow"]["environment"]={"OPENBLAS_NUM_THREADS":"1"}; path.write_text(json.dumps(doc),encoding="utf-8")
      manager=BackendManager.from_path(path); self.assertEqual(manager.engine_identity["lc0-shadow"]["environment"],{"OPENBLAS_NUM_THREADS":"1"})
  def test_child_receives_environment_override(self):
    with tempfile.TemporaryDirectory() as tmp:
      script=Path(tmp)/"env_uci.py"
      script.write_text("import os,sys\nprint('BOUND_ENV='+os.environ.get('ALLFATHER_TEST_ENV',''),file=sys.stderr,flush=True)\nfor raw in sys.stdin:\n c=raw.strip()\n if c=='uci': print('id name EnvFake\\noption name UCI_Chess960 type check default false\\nuciok',flush=True)\n elif c=='isready': print('readyok',flush=True)\n elif c=='quit': break\n",encoding="utf-8")
      p=UciProcess(name="env-fake",binary=Path(sys.executable),cwd=ROOT,args=[str(script)],environment={"ALLFATHER_TEST_ENV":"online2-bound"},timeout=3)
      try:
        p.start(); deadline=time.monotonic()+2
        while time.monotonic()<deadline and "BOUND_ENV=online2-bound" not in p.stderr_tail: time.sleep(.01)
        self.assertIn("BOUND_ENV=online2-bound",p.stderr_tail); p.configure({"UCI_Chess960":False})
      finally: p.close()
if __name__=="__main__": unittest.main()
