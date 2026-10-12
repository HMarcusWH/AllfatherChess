"""RELEASE-1 hermetic policy, OCI, runtime and aggregate negative controls."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def script(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    assert spec and spec.loader
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

builder=script("r1builder","scripts/build-release-candidate.py")
verify=script("r1verify","scripts/verify-release-candidate.py")
oci=script("r1oci","scripts/verify-release-oci.py")
agg=script("r1aggregate","scripts/qualify-release-aggregate.py")
runtime=script("r1runtime","scripts/qualify-release-runtime.py")

class PolicyTests(unittest.TestCase):
    def test_frozen_inputs_have_no_deployment_authority(self):
        policy,lic,qual=builder.parse_inputs()
        self.assertEqual(policy["platform"],"linux/amd64")
        self.assertEqual(policy["ubuntu_snapshot"],"20261001T000000Z")
        self.assertFalse(lic["publication_allowed"])
        self.assertFalse(qual["public_release_gate"])

    def test_build_context_prevents_source_secret_copy(self):
        df=(ROOT/"deploy/release/Dockerfile").read_text()
        ignore=(ROOT/"deploy/release/Dockerfile.dockerignore").read_text()
        self.assertIn("FROM ${BASE_IMAGE}",df)
        self.assertIn("USER 10001:10001",df)
        self.assertIn('CMD ["verify"]',df)
        self.assertIn("COPY build/release1-stage/bridge/",df)
        self.assertNotIn("LICHESS_BOT_TOKEN",df)
        self.assertTrue(ignore.startswith("#"))
        self.assertIn("**",ignore)
        self.assertNotIn("!engines/",ignore)

    def test_symlinks_and_secret_candidates_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ("config.yml","account.key","identity.pem"):
                p=root/name
                p.write_text("secret")
                with self.subTest(path=name),self.assertRaises(ValueError):
                    builder.clean_file(root,p)
            target=root/"file.py"
            target.write_text("x")
            link=root/"linked.py"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                builder.clean_file(root,link)

    def test_missing_manifest_never_qualifies(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                verify.verify(manifest_path=Path(d)/"missing.json",stage=Path(d))

class RuntimeTests(unittest.TestCase):
    def fixture(self):
        return {"Config":{"User":"10001:10001","Entrypoint":[
                    "/opt/online3b/bin/python","/opt/release/entrypoint.py"]},
                "HostConfig":{"NetworkMode":"none","ReadonlyRootfs":True,
                    "CapDrop":["ALL"],"SecurityOpt":["no-new-privileges"],
                    "PidsLimit":128,"Memory":6*1024**3,"NanoCpus":4*10**9,
                    "Privileged":False,"PortBindings":{}},
                "Mounts":[{"Destination":"/work","RW":False},
                          {"Destination":"/var/lib/allfather","RW":True},
                          {"Destination":"/opt/allfather/build/replays-online-hybrid-v2",
                           "RW":True}]}

    def test_real_mount_and_limits_contract(self):
        d=self.fixture()
        self.assertTrue(runtime.check(d,source_commit="a"*40,case="R00-white")["root_read_only"])
        for field,value in (("NetworkMode","bridge"),("ReadonlyRootfs",False),
                            ("CapDrop",[]),("SecurityOpt",[]),("Memory",0),
                            ("NanoCpus",0),("PidsLimit",0),("Privileged",True)):
            bad=copy.deepcopy(d)
            bad["HostConfig"][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                runtime.check(bad,source_commit="a"*40,case="R00-white")
        bad=copy.deepcopy(d)
        bad["Config"]["User"]="root"
        with self.assertRaises(ValueError):
            runtime.check(bad,source_commit="a"*40,case="R00-white")

def synthetic_oci(root,*,bad_user=False,missing_sbom=False):
    members={}
    def blob(raw):
        digest=hashlib.sha256(raw).hexdigest()
        members["blobs/sha256/"+digest]=raw
        return {"digest":"sha256:"+digest,"size":len(raw)}
    cfg=blob(json.dumps({"config":{"User":"root" if bad_user else "10001:10001",
           "Entrypoint":["/opt/online3b/bin/python","/opt/release/entrypoint.py"],
           "Cmd":["verify"],"Env":["PIP_NO_INDEX=1"],
           "Labels":{"org.opencontainers.image.revision":"b"*40}}}).encode())
    layer=blob(b"tiny synthetic layer")
    image=blob(json.dumps({"schemaVersion":2,"config":cfg,"layers":[layer]}).encode())
    image["platform"]={"os":"linux","architecture":"amd64"}
    kinds=["https://slsa.dev/provenance/v1"]
    if not missing_sbom:kinds.append("https://spdx.dev/Document")
    statements=[blob(json.dumps({"predicateType":k}).encode()) for k in kinds]
    att=blob(json.dumps({"schemaVersion":2,"config":cfg,"layers":statements}).encode())
    att["annotations"]={"vnd.docker.reference.type":"attestation-manifest"}
    att["platform"]={"os":"unknown","architecture":"unknown"}
    members["index.json"]=json.dumps({"schemaVersion":2,
                                     "manifests":[image,att]}).encode()
    members["oci-layout"]=b'{"imageLayoutVersion":"1.0.0"}'
    dest=root/"candidate.tar"
    with tarfile.open(dest,"w") as t:
        for name,raw in members.items():
            info=tarfile.TarInfo(name)
            info.size=len(raw)
            t.addfile(info,io.BytesIO(raw))
    return dest

class OCIArchiveTests(unittest.TestCase):
    def test_exact_digest_and_attestations(self):
        with tempfile.TemporaryDirectory() as d:
            path=synthetic_oci(Path(d))
            result=oci.inspect(path,source_commit="b"*40)
            self.assertTrue(result["sbom_attestation_present"])
            self.assertTrue(result["provenance_attestation_present"])

    def test_missing_sbom_and_nonroot_requirements(self):
        with tempfile.TemporaryDirectory() as d:
            for user,sbom in ((True,False),(False,True)):
                path=synthetic_oci(Path(d),bad_user=user,missing_sbom=sbom)
                with self.subTest(user=user,missing_sbom=sbom),self.assertRaises(ValueError):
                    oci.inspect(path,source_commit="b"*40)

class AggregateTests(unittest.TestCase):
    def fixture(self):
        sha="c"*40
        policy=json.loads((ROOT/"qualification/release-qualification-v1.json").read_text())
        cases=[{"scenario":name,"source_commit":sha,
               "root_read_only":True,"network_none":True,"nonroot_user":True,
               "bounded_resources":True,"durable_journal_mount":True,
               "separate_replay_mount":True} for name in policy["integration_scenarios"]]
        return dict(source_sha=sha,
             stage={"source_commit":sha,"engine_release_verified":True,
                    "bridge_verified":True,"python_wheels_verified":True,
                    "inventory_files":400},
             image={"source_commit":sha,"engine_release_verified":True,
                    "bridge_verified":True,"image_mode":True},
             oci={"source_commit":sha,"sbom_attestation_present":True,
                  "provenance_attestation_present":True,
                  "offline_artifact_verified":True,
                  "oci_manifest_digest":"sha256:"+"d"*64,
                  "oci_config_digest":"sha256:"+"e"*64,
                  "oci_archive_sha256":"f"*64},
             host={"destination_host_qualified":False,
                   "actual_destination_host_id":None},
             online={"source_commit":sha,"harness_qualified":True,
                     "containment_qualified_offline":True,
                     "recovery_qualified_offline":True,"validated_run_count":10,
                     "qualification_failures":[]},
             license={"status":"REVIEW_REQUIRED",
                      "publication_allowed":False,"legal_approval":False},
             policy=policy,runtime_cases=cases,
             inventory={"uid":10001,"public_bot_release":False,
                        "os_packages":["pkg="+str(i) for i in range(20)]})

    def test_qualified_offline_still_blocks_promotion(self):
        report=agg.qualify(**self.fixture())
        self.assertTrue(report["offline_candidate_verified"])
        self.assertFalse(report["promotion_authorized"])
        self.assertFalse(report["public_distribution_authorized"])

    def test_missing_case_unapproved_license_or_recovery_fails(self):
        cases=[
            lambda x:x["runtime_cases"].pop(),
            lambda x:x["license"].update(legal_approval=True),
            lambda x:x["online"].update(recovery_qualified_offline=False)
        ]
        for mutate in cases:
            data=self.fixture()
            mutate(data)
            with self.subTest(mutation=mutate),self.assertRaises(ValueError):
                agg.qualify(**data)

if __name__=="__main__":
    unittest.main()
