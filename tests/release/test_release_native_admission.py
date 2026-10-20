"""Exact pre-install native SHA gate: wrong bytes must never invoke apt."""
import hashlib,os,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"deploy/release/preinstall-native-gate.sh"
COMPILER=ROOT/"scripts/compile-release-native-selection.py"
class NativePreinstallTests(unittest.TestCase):
    def test_manifest_compiles_directly_from_source_candidate_lock(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("native_compiler",COMPILER)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        files=m.compile_lock(ROOT/"qualification/release-os-packages.lock")
        self.assertEqual(len(files["bootstrap"].splitlines()),3)
        self.assertEqual(len(files["runtime"].splitlines()),26)
        self.assertTrue(all(len(s.split("\t"))==5
                            for raw in files.values() for s in raw.splitlines()))
    def fixture(self, folder):
        archive=folder/"archives";archive.mkdir()
        manifest=folder/"manifest";manifest.mkdir()
        evidence=folder/"evidence";evidence.mkdir()
        binpath=folder/"fakebin";binpath.mkdir()
        seen=folder/"apt-invoked"
        fake=binpath/"apt-get"
        fake.write_text("#!/bin/sh\nprintf invoked > \"$APT_CALLED\"\nexit 0\n")
        fake.chmod(0o755)
        lines=[]
        for name,arch in (("nativea","amd64"),("nativeb","amd64"),("nativec","all")):
            package=folder/("p-"+name);(package/"DEBIAN").mkdir(parents=True)
            (package/"DEBIAN/control").write_text(
                f"Package: {name}\nVersion: 1.0\nArchitecture: {arch}\n"
                "Maintainer: nobody <nobody@example.invalid>\n"
                "Description: native test stub\n")
            filename=f"{name}_1.0_{arch}.deb"
            out=archive/filename
            subprocess.run(["dpkg-deb","--build",str(package),str(out)],
                           check=True,capture_output=True,timeout=10)
            sha=hashlib.sha256(out.read_bytes()).hexdigest()
            lines.append([name,"1.0",arch,sha,filename])
        lines.sort()
        (manifest/"bootstrap.tsv").write_text(
            "".join("\t".join(row)+"\n" for row in lines))
        env={**os.environ,"NATIVE_MANIFEST_DIR":str(manifest),
             "NATIVE_ARCHIVE_DIR":str(archive),"NATIVE_EVIDENCE_DIR":str(evidence),
             "APT_CALLED":str(seen),"PATH":str(binpath)+":"+os.environ["PATH"]}
        return archive,manifest,evidence,env,seen,lines
    def run_gate(self,env,action):
        return subprocess.run(["/bin/sh",str(SCRIPT),"bootstrap",action],
                              env=env,capture_output=True,text=True,timeout=15)
    def test_positive_accepted_and_only_verified_archives_installed(self):
        with tempfile.TemporaryDirectory() as d:
            archive,manifest,evidence,env,seen,_=self.fixture(Path(d))
            r=self.run_gate(env,"--install")
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertTrue(seen.is_file())
            self.assertEqual((evidence/"bootstrap-preinstall-verified.tsv").read_bytes(),
                             (manifest/"bootstrap.tsv").read_bytes())
    def test_mutated_bytes_block_before_any_apt_execution(self):
        for poison in ("archive-byte","wrong-sha","missing","extra","symlink","wrong-arch"):
            with self.subTest(poison=poison),tempfile.TemporaryDirectory() as d:
                archive,manifest,evidence,env,seen,records=self.fixture(Path(d))
                data=[x.copy() for x in records]
                target=archive/records[0][4]
                if poison=="archive-byte":
                    target.write_bytes(target.read_bytes()+b"x")
                elif poison=="wrong-sha":
                    data[0][3]="a"*64
                elif poison=="missing":target.unlink()
                elif poison=="extra":(archive/"unapproved_1.0_amd64.deb").write_bytes(b"fake")
                elif poison=="symlink":
                    original=target.read_bytes()
                    elsewhere=Path(d)/"moved.deb";elsewhere.write_bytes(original)
                    target.unlink();target.symlink_to(elsewhere)
                elif poison=="wrong-arch":data[0][2]="all"
                (manifest/"bootstrap.tsv").write_text(
                    "".join("\t".join(x)+"\n" for x in data))
                r=self.run_gate(env,"--install")
                self.assertNotEqual(r.returncode,0,r.stdout)
                self.assertFalse(seen.exists(),"Native package execution happened before SHA check")
    def test_partial_manifest_denied_even_if_all_bytes_match(self):
        with tempfile.TemporaryDirectory() as d:
            _,manifest,_,env,seen,records=self.fixture(Path(d))
            (manifest/"bootstrap.tsv").write_text(
                "".join("\t".join(x)+"\n" for x in records[:2]))
            r=self.run_gate(env,"--install")
            self.assertNotEqual(r.returncode,0)
            self.assertFalse(seen.exists())
if __name__=="__main__":unittest.main()
